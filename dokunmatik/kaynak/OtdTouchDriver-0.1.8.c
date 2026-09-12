#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/errno.h>
#include <linux/usb.h>
#include <linux/input.h>
#include <linux/usb/input.h>
#include <linux/hid.h>
#include <linux/delay.h>
#include <linux/cdev.h>
#include <asm/uaccess.h>
#include <linux/input/mt.h>

#define DRIVER_VERSION  "1.7.0.5488"
#define DRIVER_DESC "USB touchscreen driver for multitouch"
#define DRIVER_LICENSE "GPL"

#define MAXSUPPORTPOINT   10

#define MAJOR_DEVICE_NUMBER 45
#define MINOR_DEVICE_NUMBER 193
#define USB_OTDUSB_VENDOR_ID  0x2621
#define USB_OTDUSB_PRODUCT_ID 0x2201
#define TOUCH_DEVICE_NAME "OtdOpticTouch"
#define ODT_TOUCH_POINT_COUNT 4

//control code
#define CTLCODE_SET_REPORT 0x40
#define CTLCODE_GET_REPORT 0x41
#define CTLCODE_PKT_LENGTH 0x42

#define CTLCODE_START_CALIB 0xF0
#define CTLCODE_EXITMODE_CALIB 0xF1
#define CTLCODE_GET_POINT 0xF2
#define CTLCODE_UPDATE_CALIB 0xF3
#define CTLCODE_SAVEPARAM_CALIB 0xF4
#define CTLCODE_LOADPARAM_CALIB 0xF5
#define CTLCODE_STOP_CALIB 0xF6

#define ODT_TOUCH_STATUS_DOWN 0x07
#define ODT_TOUCH_STATUS_UP 0x04

#define err(format, arg...)					\
	printk(KERN_ERR KBUILD_MODNAME ": " format "\n", ##arg)


#pragma pack(1)
/*
  struct touch_points
  {
  unsigned char valid;
  int	x;
  int y;
  int radius;
  unsigned char isExist;
  };
*/
typedef struct _X_RectangleF
{
	float MinX;
	float MaxX;
	float MinY;
	float MaxY;
}
	X_RectangleF, *PX_RectangleF;

typedef struct _CALIB_PARAM
{
	unsigned char	CalibMode;
	X_RectangleF	Region;
	union
	{
		struct
		{
			struct
		 	{
				double	A00;
				double	A01;
				double	A10;
				double	A11;
			}	X,Y;
		}		Simple;
		struct
		{
			struct
			{
				double	A00;
				double	A01;
				double	A10;
				double	A11;
				double	A02;
				double	A20;
				double	A03;
				double	A12;
				double	A21;
				double	A30;
			}	X,Y;
		}		Distorted;
	}			Param;
	double			RadiusFactor;
}
	CALIB_PARAM, *PCALIB_PARAM;

typedef struct _ODT_CALIB_PARAM
{
	CALIB_PARAM		Param;
	unsigned char	Valid;
}ODT_CALIB_PARAM, *PODT_CALIB_PARAM;

struct touch_points
{
	unsigned char State;
	unsigned char ID;
	short X;
	short Y;
	short Width;
	short Height;
};
#pragma pack()
struct hid_touch_report
{
	unsigned char ReportID;
	struct touch_points TouchPoints[ODT_TOUCH_POINT_COUNT];
	unsigned short ScanTime;
	unsigned char ActualCount;
};
struct touch_points_buf
{
	struct touch_points points;
	char   flagExit;
};

/*
 * Circular Queue solves ohci-hcd problems of AMD Interactive White Boards.
 * Driver works fine with ehci and xhci, but it fails to keep track of
 * touching fingers when it comes to ohci. Originally the driver would get
 * 64 bytes from usb_interrupt_msg and server daemon would get this 64-byte
 * chunk through the read system call. By the help of a circular queue,
 * the driver gets multiple of 64 bytes and delivers a single 64-byte chunk
 * to the server when it calls read system call.
 * Gokhan Karabulut <gokhan.karabulut@tubitak.gov.tr>
 */

#define CAPACITY (16)
#define BUFFER_SIZE (64)
#define NPACKETS (2)

struct cqueue
{
	struct inbuff
	{
		char buffer[BUFFER_SIZE];
	}inbuffs[CAPACITY];

	int front;
	int rear;
	int length;
};

static struct cqueue cqueue;

static void
cqueue_init(struct cqueue* q)
{
	q->front = 0;
	q->rear = 0;
	q->length = 0;
}

static int
is_empty(const struct cqueue *q)
{
	return (q->length == 0);
}

static int
is_full(const struct cqueue *q)
{
	return (q->length == CAPACITY);
}

static int
get_length(const struct cqueue *q)
{
	return q->length;
}

static void
enqueue(struct cqueue *q, const char *buff)
{
	if (!is_full(q)) {
		memcpy(q->inbuffs[q->rear].buffer, buff, BUFFER_SIZE);
		q->rear = (q->rear + 1) % CAPACITY;
		q->length++;
	} else {
		printk(KERN_WARNING "Packet not enqueued");
	}
}

static void
dequeue(struct cqueue *q, char *buff)
{
	if (!is_empty(q)) {
		memcpy(buff, q->inbuffs[q->front].buffer, BUFFER_SIZE);
		q->front = (q->front + 1) % CAPACITY;
		q->length--;
	} else {
		printk(KERN_WARNING "Packet not dequeued");
	}
}

struct otdusb{
	char name[128];
	char phys[64];

	char maxPoint[MAXSUPPORTPOINT];

	unsigned char isDown[2];
	char inBuff[64];
	bool bStartCalib;
	bool bStopCalibMode;
	bool bUpdate;
	bool bStopCalib;
	ODT_CALIB_PARAM CalibParam;
	unsigned char cmdLen;

	struct usb_device *udev;
	struct input_dev *dev;
	struct touch_points rawPoints;
};

int pipe = 0;
struct cdev otd_cdev;
struct otdusb *otdgb;
static struct class *otd_class;
static struct usb_driver otd_driver;
static struct file_operations otd_fops;

/*
  static struct usb_class_driver otd_class_driver = {
  .name = TOUCH_DEVICE_NAME,
  .fops = &otd_fops,
  .minor_base = MINOR_DEVICE_NUMBER,
  };
*/
static struct usb_device_id otd_table[] = {
	{USB_DEVICE(USB_OTDUSB_VENDOR_ID, USB_OTDUSB_PRODUCT_ID)},
	{USB_DEVICE(0x2621, 0x4501)},
	{	}
};

MODULE_DEVICE_TABLE(usb, otd_table);

static int otd_open(struct inode * inode, struct file * filp)
{
	struct otdusb * otd;
	/*
	  struct usb_interface * intf;
	  int subminor;
	  subminor = iminor(inode);
	  //intf = usb_find_interface(&otd_driver, MINOR_DEVICE_NUMBER);
	  intf = usb_find_interface(&otd_driver, subminor);
	  if (!intf) {
	  err("OTD:    %s usb_find_interface ERROR!",__func__);
	  return -1;
	  }

	  otd = usb_get_intfdata(intf);
	*/
	otd = otdgb;
	if (!otd) {
		err("OTD:    %s touch is NULL!",__func__);
		return -1;
	}

	filp->private_data = otd;

	return 0;
}

static int otd_release(struct inode * inode, struct file * filp)
{
	return 0;
}

static int otd_read(struct file * filp, char * buffer, size_t count, loff_t * ppos)
{
	int retval = -1;
	int bytes_read;
	struct otdusb * otd;
	char inbuff[NPACKETS*BUFFER_SIZE];
	int npackets = 0;
	int i;

	if (NULL == filp->private_data) {
		err("OTD:   %s private_data is NULL!",__func__);
		return retval;
	}

	otd = filp->private_data;

	if (CAPACITY - get_length(&cqueue) >= NPACKETS) {
		retval = usb_interrupt_msg(otd->udev,
					   pipe,
					   inbuff,
					   NPACKETS*BUFFER_SIZE,
					   &bytes_read, 1000);
		if (retval == -110) {
			/* printk(KERN_INFO "read: return 0"); */
			return 0;
		}

		npackets = bytes_read / BUFFER_SIZE;
		for (i = 0; i < npackets; ++i) {
			enqueue(&cqueue, &inbuff[i*BUFFER_SIZE]);
			/* printk(KERN_INFO "read: enqueue"); */
		}
	}
	if (!is_empty(&cqueue)) {
		dequeue(&cqueue, otd->inBuff);
		bytes_read = BUFFER_SIZE;
		retval = 0;
		/* printk(KERN_INFO "read: dequeue"); */
	}

	if (retval == 0 && bytes_read > 0) {
		if (otd->bStartCalib) {
			err("OTD: startcalib in read\n");
			otd->inBuff[0] = CTLCODE_START_CALIB;//0xF0;
			otd->bStartCalib = false;
		}
		if (otd->bStopCalibMode) {
			err("OTD: stopcalib in read\n  ");
			otd->inBuff[0] = CTLCODE_EXITMODE_CALIB;//0xF1;
			otd->bStopCalibMode = false;
		}
		if (otd->bUpdate) {
			err("OTD: bupdate in read\n  ");
			otd->inBuff[0] = CTLCODE_UPDATE_CALIB;//0xF3;
			otd->bUpdate = false;
		}
		if (otd->bStopCalib) {
			otd->inBuff[0] = CTLCODE_STOP_CALIB;
			otd->bStopCalib = false;
		}
		if (copy_to_user(buffer, otd->inBuff, bytes_read)) {
			err("OTD:  %s copy_to_user failed!",__func__);
			retval = -EFAULT;
		} else {
			retval = bytes_read;
		}
	} else if (retval == -110) {
		/* printk(KERN_INFO "read -110"); */
		retval = 0;
	}
	/* printk(KERN_INFO "read %d", retval); */
	return retval;
}

static int otd_write(struct file * filp, const char * user_buffer, size_t count, loff_t * ppos)
{
	int i = 0;
	int ret = -1;
	struct otdusb *otd;
	struct input_dev *dev;
	struct hid_touch_report report;
	struct touch_points points[2];
	int counts = 0;
	if (NULL == filp->private_data) {
		err("OTD:    %s private_data is NULL!",__func__);
		return ret;
	}
	otd = filp->private_data;
	dev = otd->dev;
	memset(points, 0, sizeof(struct touch_points));
	memset(&otd->rawPoints, 0, sizeof(struct touch_points));

	//ret = copy_from_user(points, user_buffer, sizeof(points));
	ret = copy_from_user(&report, user_buffer, sizeof(report));
	memcpy(points, report.TouchPoints, sizeof(points));
	if (ret != 0) {
		err("OTD:    %s copy_to_user failed!",__func__);
	}
	//err("OTD:  check the report  ReportID %d ,Scantime %d, Actualcount %d ", report.ReportID, report.ScanTime, report.ActualCount);

	for (i = 0; i < 2; i++) {
		if (points[i].State == ODT_TOUCH_STATUS_DOWN) {
			otd->rawPoints = points[0];
			input_mt_slot(dev, points[i].ID);
			//err("OTD:  check the report ID %d ", points[i].ID);
			input_mt_report_slot_state(dev, MT_TOOL_FINGER, true);
			input_report_abs(dev, ABS_MT_TOUCH_MAJOR, points[i].Width);
			input_report_abs(dev,ABS_MT_POSITION_X, points[i].X);
			input_report_abs(dev,ABS_MT_POSITION_Y, points[i].Y);
			counts++;
		}
		if (points[i].State == ODT_TOUCH_STATUS_UP) {
			otd->rawPoints = points[0];
			input_mt_slot(dev, points[i].ID);
			input_mt_report_slot_state(dev, MT_TOOL_FINGER, false);
			points[i].State = 0;
			counts++;
		}
	}
	//if (!counts)
	input_sync(dev);
	/***********
	{
	err("OTD:  check the report  State %d ,Width %d, Height %d ", report.TouchPoints[i].State, report.TouchPoints[i].Width,report.TouchPoints[i].Height);
		if (points[i].State == ODT_TOUCH_STATUS_DOWN) {
			otd->rawPoints = points[0];

			err("Click the point is X:%d \t %d \t %d", points[i].X, points[i].Y, 5);
			if (points[i].X < 0) {
				points[i].X = 0;
			} else if (points[i].X > 32767) {
				points[i].X = 32767;
			}

			if (points[i].Y < 0) {
				points[i].Y = 0;
			} else if (points[i].Y > 32767) {
				points[i].Y = 32767;
			}

			//input_report_abs(dev,ABS_MT_TRACKING_ID, points[i].ID);
			input_report_abs(dev, ABS_MT_SLOT, points[i].ID);
			input_mt_report_slot_state(dev, MT_TOOL_FINGER, 1);
			input_report_abs(dev,ABS_MT_POSITION_X, points[i].X);
			input_report_abs(dev,ABS_MT_POSITION_Y, points[i].Y);
			input_mt_sync(dev);
			counts ++;
			//input_report_abs(dev,ABS_MT_TOUCH_MAJOR, max);
			//input_report_abs(dev,ABS_MT_TOUCH_MINOR, points[i].X);
			//input_report_abs(dev,ABS_MT_PRESSURE,255);
			//input_report_key(dev,BTN_TOUCH,1);
		} else if (points[i].State == ODT_TOUCH_STATUS_UP) {
			otd->rawPoints = points[0];
			input_report_abs(dev, ABS_MT_SLOT, points[i].ID);
			input_mt_report_slot_state(dev, MT_TOOL_FINGER, 0);
		}
	}
	if (!counts)
	    input_mt_sync(dev);
	**/
	//input_sync(dev);

	return 0;
}

static long otd_ioctl(struct file * filp, unsigned int ctl_code, unsigned long ctl_param)
{
	unsigned char buf[64];
	int ret = -1;
	struct otdusb *otd;
	struct usb_device *udev;

	memset(buf,0,sizeof(buf));

	if (NULL == filp->private_data) {
		err("OTD:    %s private_data is NULL!",__func__);
		return ret;
	}

	otd = filp->private_data;
	udev = otd->udev;

	switch(ctl_code) {
	case CTLCODE_PKT_LENGTH:
		ret = copy_from_user((unsigned char *)&otd->cmdLen, (int *)ctl_param, sizeof(unsigned char));
		break;
	case CTLCODE_SET_REPORT:
		ret = copy_from_user((unsigned char *)buf, (unsigned char *)ctl_param, otd->cmdLen);
		if (ret == 0) {
			ret = usb_control_msg(udev,
					      usb_sndctrlpipe(udev, 0),
					      0,
					      0x40,
					      0,
					      0,
					      (char *)buf,
					      otd->cmdLen,
					      1000);
		}
		break;
	case CTLCODE_GET_REPORT:
		ret = usb_control_msg(udev,
				      usb_rcvctrlpipe(udev, 0),
				      0,
				      0xc0,
				      0,
				      0,
				      (char *)buf,
				      otd->cmdLen,
				      1000);
		if (ret > 0) {
			ret = copy_to_user((unsigned char *)ctl_param, (unsigned char *)buf, otd->cmdLen);
		}
		break;
	case CTLCODE_START_CALIB:
		err("OTD: CTLCODE_START_CALIB:in\n");
		ret = copy_from_user((bool *)&otd->bStartCalib, (bool *)ctl_param, sizeof(bool));
		if (otd->bStartCalib) {
			err("OTD: CTLCODE_START_CALIB: is true\n");
		} else {
			err("OTD: CTLCODE_START_CALIB: is false\n");
		}

		break;
	case CTLCODE_EXITMODE_CALIB:
		ret = copy_from_user((bool *)&otd->bStopCalibMode, (bool *)ctl_param, sizeof(bool));
		break;
	case CTLCODE_GET_POINT:
		ret = copy_to_user((struct touch_points *)ctl_param, (struct touch_points *)&otd->rawPoints, sizeof(struct touch_points));
		break;
	case CTLCODE_UPDATE_CALIB:
		err("OTD: CTLCODE_UPDATA_CALIB:in\n");
		ret = copy_from_user((bool *)&otd->bUpdate, (bool *)ctl_param, sizeof(bool));
		break;
	case CTLCODE_SAVEPARAM_CALIB:
		err("OTD: CTLCODE_SAVEPARAM_CALIB:in\n");
		ret = copy_from_user((PODT_CALIB_PARAM)&otd->CalibParam, (PODT_CALIB_PARAM)ctl_param, sizeof(ODT_CALIB_PARAM));
		err("OTD: CTLCODE_SAVEPARAM_CALIB %d  %d\n", ret, otd->CalibParam.Valid);
		break;
	case CTLCODE_LOADPARAM_CALIB:
		ret = copy_to_user((PODT_CALIB_PARAM)ctl_param, (PODT_CALIB_PARAM)&otd->CalibParam, sizeof(ODT_CALIB_PARAM));
		break;
	case CTLCODE_STOP_CALIB:
		ret = copy_from_user((bool *)&otd->bStopCalib, (bool *)ctl_param, sizeof(bool));
		break;
	}
	return ret;
}

static struct file_operations otd_fops = {
	.owner = THIS_MODULE,
	.open = otd_open,
	.read = otd_read,
	.write = otd_write,
	.unlocked_ioctl = otd_ioctl,
	.release = otd_release,
};

static int otd_open_device(struct input_dev * dev)
{
	return 0;
}

static void otd_close_device(struct input_dev * dev)
{
	//dbg("OTD:  otd_close_device+++\n");
}

static bool otd_mkdev(void)
{
	int retval;

	msleep(3000);

	//create device node
	dev_t otd_devno = MKDEV(MAJOR_DEVICE_NUMBER,MINOR_DEVICE_NUMBER);

	retval = register_chrdev_region(otd_devno,1,TOUCH_DEVICE_NAME);
	if (retval < 0) {
		err("OTD:    %s register chrdev error.",__func__);
		return false;
	}

	cdev_init(&otd_cdev,&otd_fops);
	otd_cdev.owner = THIS_MODULE;
	otd_cdev.ops = &otd_fops;
	retval = cdev_add(&otd_cdev,otd_devno,1);

	if (retval) {
		err("OTD:    %s  Adding char_reg_setup_cdev error=%d",
		    __func__,retval);
		return false;
	}

	otd_class = class_create(THIS_MODULE, TOUCH_DEVICE_NAME);
	if (IS_ERR(otd_class)) {
		err("OTD:    %s class create failed.",__func__);
		return false;
	}

	device_create(otd_class, NULL,
		      MKDEV(MAJOR_DEVICE_NUMBER, MINOR_DEVICE_NUMBER),
		      NULL, TOUCH_DEVICE_NAME);

	return true;
}

static int otd_probe(struct usb_interface * intf, const struct usb_device_id *id)
{
	int i;
	int retval = -ENOMEM;
	struct usb_host_interface * interface = intf->cur_altsetting;
	struct usb_endpoint_descriptor * endpoint = NULL;
	struct usb_device * udev = interface_to_usbdev(intf);
	struct input_dev * dev = NULL;
	struct otdusb * otd;
	cqueue_init(&cqueue);
	otd = kzalloc(sizeof(struct otdusb), GFP_KERNEL);
	if (!otd) {
		err("OTD:    %s Out of memory.",__func__);
		return -ENOMEM;
	}
	memset(otd, 0, sizeof(struct otdusb));
	otdgb = otd;
	for (i = 0; i < interface->desc.bNumEndpoints; i++) {
		endpoint = &interface->endpoint[i].desc;
		if (endpoint->bEndpointAddress & USB_DIR_IN) {
			pipe = usb_rcvintpipe(udev, endpoint->bEndpointAddress);
		}
	}

	dev = input_allocate_device();
	if (!dev)
		goto EXIT;

	if (udev->manufacturer)
		strlcpy(otd->name, udev->manufacturer, sizeof(otd->name));

	if (udev->product) {
		if (udev->manufacturer)
			strlcat(otd->name, " ", sizeof(otd->name));
		strlcat(otd->name, udev->product, sizeof(otd->name));
	}

	if (!strlen(otd->name))
		snprintf(otd->name, sizeof(otd->name),
			 "Optical Touchscreen %04x:%04x",
			 le16_to_cpu(udev->descriptor.idVendor),
			 le16_to_cpu(udev->descriptor.idProduct));

	usb_make_path(udev, otd->phys, sizeof(otd->phys));
	strlcat(otd->phys, "/input0", sizeof(otd->phys));

	dev->name = otd->name;
	dev->phys = otd->phys;

	usb_to_input_id(udev, &dev->id);
	dev->dev.parent = &intf->dev;

	input_set_drvdata(dev, otd);

	dev->open = otd_open_device;
	dev->close = otd_close_device;

	dev->evbit[0] = BIT(EV_KEY) | BIT(EV_ABS);
	set_bit(BTN_TOUCH, dev->keybit);
	set_bit(EV_SYN, dev->evbit);
	set_bit(EV_KEY, dev->evbit);
	set_bit(EV_ABS, dev->evbit);
	dev->absbit[0] = BIT(ABS_MT_POSITION_X) | BIT(ABS_MT_POSITION_Y);
	set_bit(ABS_PRESSURE, dev->absbit);

	input_mt_init_slots(dev, ODT_TOUCH_POINT_COUNT,0);
	//input_mt_init_slots(dev, ODT_TOUCH_POINT_COUNT);
	input_set_abs_params(dev, ABS_X,0,32767,0,0);
	input_set_abs_params(dev, ABS_Y,0,32767,0,0);
	input_set_abs_params(dev, ABS_MT_PRESSURE,0,1,0,0);
	input_set_abs_params(dev, ABS_MT_POSITION_X, 0, 32767, 0, 0);
	input_set_abs_params(dev, ABS_MT_POSITION_Y, 0, 32767, 0, 0);
	input_set_abs_params(dev, ABS_MT_TOUCH_MAJOR, 0, 255, 0, 0);

	otd->udev = udev;//usb_get_dev(udev);
	otd->dev = dev;

	retval = input_register_device(otd->dev);
	if (retval) {
		err("OTD:    %s input_register_device failed,retval:%d",
		    __func__,retval);
		goto EXIT;
	}

	usb_set_intfdata(intf, otd);
	/*
	if (0) {
		retval = usb_register_dev(intf, &otd_class_driver);
		if (retval) {
			err("OTD:
			%s Not able to get a minor for this device.",__func__);
			usb_set_intfdata(intf,NULL);
			goto EXIT;
		}
	}
	*/
	retval = otd_mkdev();
	if (!retval) {
		goto EXIT;
	}

	return 0;

EXIT:
	if (dev)
		input_free_device(dev);

	if (otd)
		kfree(otd);

	return -ENOMEM;
}

static void otd_disconnect(struct usb_interface * intf)
{
	struct otdusb * otd = usb_get_intfdata(intf);

	dev_t otd_devno = MKDEV(MAJOR_DEVICE_NUMBER,MINOR_DEVICE_NUMBER);

	cdev_del(&otd_cdev);
	unregister_chrdev_region(otd_devno,1);

	device_destroy(otd_class,otd_devno);
	class_destroy(otd_class);

	//usb_deregister_dev(intf, &otd_class_driver);
	usb_set_intfdata(intf, NULL);

	if (otd) {
		input_unregister_device(otd->dev);
		kfree(otd);
		otd = NULL;
	}
}

static struct usb_driver otd_driver = {
	.name = TOUCH_DEVICE_NAME,
	.probe = otd_probe,
	.disconnect = otd_disconnect,
	.id_table = otd_table,
};

static int otd_init(void)
{
	int result;

	/*register this dirver with the USB subsystem*/
	result = usb_register(&otd_driver);
	if (result) {
		err("OTD: %s usb_register failed. Error number %d",__func__,result);
	}

	return 0;
}

static void otd_exit(void)
{
	usb_deregister(&otd_driver);
}

module_init(otd_init);
module_exit(otd_exit);

MODULE_DESCRIPTION(DRIVER_DESC);
MODULE_LICENSE(DRIVER_LICENSE);
MODULE_VERSION(DRIVER_VERSION);
MODULE_AUTHOR("OTD touch screen Co., Ltd");
