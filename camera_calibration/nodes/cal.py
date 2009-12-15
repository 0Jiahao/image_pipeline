#!/usr/bin/python

PKG = 'camera_calibration' # this package name
import roslib; roslib.load_manifest(PKG)

import rospy
import sensor_msgs.msg
import sensor_msgs.srv
import cv_bridge

import math
import os
import sys

import cv

import message_filters

ID_LOAD=101
ID_SAVE=102
ID_BUTTON1=110
ID_EXIT=200

# /wg/osx/rosCode/ros-pkg/ros-pkg/stacks/image_pipeline/image_view/preCalib

from camera_calibration.calibrator import get_corners, mk_image_points, cvmat_iterator, MonoCalibrator, StereoCalibrator
from std_msgs.msg import String
from std_srvs.srv import Empty

def mean(seq):
    return sum(seq) / len(seq)

def lmin(seq1, seq2):
    """ Pairwise minimum of two sequences """
    return [min(a, b) for (a, b) in zip(seq1, seq2)]

def lmax(seq1, seq2):
    """ Pairwise maximum of two sequences """
    return [max(a, b) for (a, b) in zip(seq1, seq2)]

class CalibrationNode:

    def __init__(self):
        lsub = message_filters.Subscriber('left', sensor_msgs.msg.Image)
        rsub = message_filters.Subscriber('right', sensor_msgs.msg.Image)
        ts = message_filters.TimeSynchronizer([lsub, rsub], 4)
        ts.registerCallback(self.handle_stereo)

        rospy.Subscriber('image', sensor_msgs.msg.Image, self.handle_monocular)
        self.set_camera_info_service = rospy.ServiceProxy("camera/set_camera_info", sensor_msgs.srv.SetCameraInfo)

        self.br = cv_bridge.CvBridge()
        self.p_mins = None
        self.p_maxs = None
        self.db = {}
        self.sc = StereoCalibrator()
        self.mc = MonoCalibrator()
        self.calibrated = False

    def mkgray(self, msg):
        """
        Convert a message into a bgr8 OpenCV bgr8 *monochrome* image.
        Deal with bayer images by converting to color, then to monochrome.
        """
        if 'bayer' in msg.encoding:
            msg.encoding = "mono8"
            raw = self.br.imgmsg_to_cv(msg)
            rgb = cv.CreateMat(raw.rows, raw.cols, cv.CV_8UC3)
            mono = cv.CreateMat(raw.rows, raw.cols, cv.CV_8UC1)
            cv.CvtColor(raw, rgb, cv.CV_BayerRG2BGR)
            cv.CvtColor(rgb, mono, cv.CV_BGR2GRAY)
            cv.CvtColor(mono, rgb, cv.CV_GRAY2BGR)
        else:
            rgb = self.br.imgmsg_to_cv(msg, "bgr8")

        return rgb

    def handle_monocular(self, msg):

        rgb = self.mkgray(msg)
        (self.width, self.height) = cv.GetSize(rgb)
        scrib = rgb

        scale = int(math.ceil(self.width / 640))
        if scale != 1:
            scrib = cv.CreateMat(self.height / scale, self.width / scale, cv.GetElemType(rgb))
            cv.Resize(rgb, scrib)
        else:
            scrib = cv.CloneMat(rgb)

        if not self.calibrated:
            (ok, corners) = get_corners(rgb, refine = False)
            if ok:
                # Compute some parameters for this chessboard
                Xs = [x for (x, y) in corners]
                Ys = [y for (x, y) in corners]
                p_x = mean(Xs) / self.width
                p_y = mean(Ys) / self.height
                p_size = (max(Xs) - min(Xs)) / self.width
                params = [p_x, p_y, p_size]
                if self.p_mins == None:
                    self.p_mins = params
                else:
                    self.p_mins = lmin(self.p_mins, params)
                if self.p_maxs == None:
                    self.p_maxs = params
                else:
                    self.p_maxs = lmax(self.p_maxs, params)
                is_min = [(abs(p - m) < .1) for (p, m) in zip(params, self.p_mins)]
                is_max = [(abs(p - m) < .1) for (p, m) in zip(params, self.p_maxs)]

                src = cv.Reshape(mk_image_points([corners]), 2)

                cv.DrawChessboardCorners(scrib, (8, 6), [ (x/scale, y/scale) for (x, y) in cvmat_iterator(src)], True)

                # If the image is a min or max in every parameter, add to the collection
                if any(is_min) or any(is_max):
                    self.db[str(is_min + is_max)] = (params, rgb)
        else:
            rgb_remapped = self.mc.remap(rgb)
            cv.Resize(rgb_remapped, scrib)

        self.redraw_monocular(scrib, rgb)

    def handle_stereo(self, lmsg, rmsg):

        lrgb = self.mkgray(lmsg)
        rrgb = self.mkgray(rmsg)
        (self.width, self.height) = cv.GetSize(lrgb)
        lscrib = lrgb
        rscrib = rrgb

        if not self.calibrated:
            (lok, lcorners) = get_corners(lrgb, refine = False)
            if lok:
                (rok, rcorners) = get_corners(rrgb, refine = False)
                if lok and rok:
                    # Compute some parameters for this chessboard
                    Xs = [x for (x, y) in lcorners]
                    Ys = [y for (x, y) in lcorners]
                    p_x = mean(Xs) / self.width
                    p_y = mean(Ys) / self.height
                    p_size = (max(Xs) - min(Xs)) / self.width
                    params = [p_x, p_y, p_size]
                    if self.p_mins == None:
                        self.p_mins = params
                    else:
                        self.p_mins = lmin(self.p_mins, params)
                    if self.p_maxs == None:
                        self.p_maxs = params
                    else:
                        self.p_maxs = lmax(self.p_maxs, params)
                    is_min = [(abs(p - m) < .1) for (p, m) in zip(params, self.p_mins)]
                    is_max = [(abs(p - m) < .1) for (p, m) in zip(params, self.p_maxs)]
                    
                    lscrib = cv.CloneMat(lrgb)
                    rscrib = cv.CloneMat(rrgb)
                    for (co, im) in [(lcorners, lscrib), (rcorners, rscrib)]:
                        src = cv.Reshape(mk_image_points([co]), 2)
                        cv.DrawChessboardCorners(im, (8, 6), cvmat_iterator(src), True)

                    # If the image is a min or max in every parameter, add to the collection
                    if any(is_min) or any(is_max):
                        self.db[str(is_min + is_max)] = (params, lrgb, rrgb)
        else:
            epierror = self.sc.epipolar1(lrgb, rrgb)
            if epierror == -1:
                print "Cannot find checkerboard"
            else:
                print "epipolar error:", epierror
            lscrib = self.sc.lremap(lrgb)
            rscrib = self.sc.rremap(rrgb)

        self.redraw_stereo(lscrib, rscrib, lrgb, rrgb)

    def do_calibration(self):
        self.calibrated = True
        vv = list(self.db.values())
        # vv is a list of pairs (p, i) for monocular, and triples (p, l, r) for stereo
        if len(vv[0]) == 2:
            images = [i for (p, i) in vv]
            self.mc.cal(images)
            self.mc.report()
            self.mc.ost()
        else:
            limages = [ l for (p, l, r) in vv ]
            rimages = [ r for (p, l, r) in vv ]
            self.sc.cal(limages, rimages)
            # for (i, (p, limg, rimg)) in enumerate(self.db.values()):
            #     cv.SaveImage("/tmp/cal%04d.png" % i, limg)

            self.sc.report()
            self.sc.ost()

    def do_upload(self):
        vv = list(self.db.values())
        if len(vv[0]) == 2:
            info = self.mc.as_message()
        else:
            info = self.sc.as_message()
        self.set_camera_info_service(info)

    def set_scale(self, a):
        if self.calibrated:
            vv = list(self.db.values())
            if len(vv[0]) == 2:
                self.mc.set_alpha(a)
            else:
                self.sc.set_alpha(a)

class WebCalibrationNode(CalibrationNode):
    """ Calibration node backend for a web-based UI """

    def __init__(self):
        CalibrationNode.__init__(self)
        self.img_pub = rospy.Publisher("calibration_image", sensor_msgs.msg.Image)
        self.meta_pub = rospy.Publisher("calibration_meta", String)
        self.calibration_done = rospy.Service('calibration_done', Empty, self.calibrate)

    def calibrate(self, req):
        self.do_calibration()

    def publish_meta(self):
        if not self.calibrated:
            if len(self.db) != 0:
                # Report dimensions of the n-polytope
                Ps = [v[0] for v in self.db.values()]
                Pmins = reduce(lmin, Ps)
                Pmaxs = reduce(lmax, Ps)
                vals = ['%s:%s:%s' % (label, lo, hi) for (label, lo, hi) in zip(["X", "Y", "Size"], Pmins, Pmaxs)]
                self.meta_pub.publish(String(",".join(vals)))

    def redraw_monocular(self, scrib, _):
        msg = self.br.cv_to_imgmsg(scrib, "bgr8")
        msg.header.stamp = rospy.rostime.get_rostime()
        self.img_pub.publish(msg)
        self.publish_meta()
                   

    def redraw_stereo(self, lscrib, rscrib, lrgb, rrgb):
        display = cv.CreateMat(lscrib.height, lscrib.width + rscrib.width, cv.CV_8UC3)
        cv.Copy(lscrib, cv.GetSubRect(display, (0,0,lscrib.width,lscrib.height)))
        cv.Copy(rscrib, cv.GetSubRect(display, (lscrib.width,0,rscrib.width,rscrib.height)))
        msg = self.br.cv_to_imgmsg(display, "bgr8")
        msg.header.stamp = rospy.rostime.get_rostime()
        self.img_pub.publish(msg)
        self.publish_meta()

class OpenCVCalibrationNode(CalibrationNode):
    """ Calibration node with an OpenCV Gui """

    def __init__(self):

        CalibrationNode.__init__(self)
        cv.NamedWindow("display")
        self.font = cv.InitFont(cv.CV_FONT_HERSHEY_SIMPLEX, 0.20, 1, thickness = 2, line_type = cv.CV_AA)
        #self.button = cv.LoadImage("%s/button.jpg" % roslib.packages.get_pkg_dir(PKG))
        cv.SetMouseCallback("display", self.on_mouse)
        cv.CreateTrackbar("scale", "display", 0, 100, self.on_scale)

    def on_mouse(self, event, x, y, flags, param):
        if event == cv.CV_EVENT_LBUTTONDOWN:
            if not self.calibrated:
                self.do_calibration()
            else:
                self.do_upload()

    def on_scale(self, scalevalue):
        self.set_scale(scalevalue / 100.0)

    def redraw_monocular(self, scrib, _):
        width, height = cv.GetSize(scrib)

        display = cv.CreateMat(height, width + 100, cv.CV_8UC3)
        cv.Copy(scrib, cv.GetSubRect(display, (0,0,width,height)))
        cv.Set(cv.GetSubRect(display, (width,0,100,height)), (255, 255, 255))
        #cv.Resize(self.button, cv.GetSubRect(display, (width,380,100,100)))
        self.button(cv.GetSubRect(display, (width,380,100,100)))

        if not self.calibrated:
            if len(self.db) != 0:
                # Report dimensions of the n-polytope
                Ps = [v[0] for v in self.db.values()]
                Pmins = reduce(lmin, Ps)
                Pmaxs = reduce(lmax, Ps)
                ranges = [(x-n) for (x, n) in zip(Pmaxs, Pmins)]

                for i, (label, lo, hi) in enumerate(zip(["X", "Y", "Size"], Pmins, Pmaxs)):
                    y = 100 + 100 * i
                    (w,_),_ = cv.GetTextSize(label, self.font)
                    cv.PutText(display, label, (width + (100 - w) / 2, 100 + 100 * i), self.font, (0,0,0))
                    cv.Line(display,
                            (int(width + lo * 100), y + 20),
                            (int(width + hi * 100), y + 20),
                            (0,0,0),
                            4)
        else:
            cv.PutText(display, "acc.", (width, 100), self.font, (0,0,0))

        cv.ShowImage("display", display)
        k = cv.WaitKey(6)

    def button(self, dst):
        cv.Set(dst, (255, 25, 255))
        size = cv.GetSize(dst)
        if not self.calibrated:
            color = cv.RGB(155, 100, 80)
            label = "CALIBRATE"
        else:
            color = cv.RGB(80, 155, 100)
            label = "UPLOAD"
        cv.Circle(dst, (size[0] / 2, size[1] / 2), min(size) / 2, color, -1)
        ((w, h), _) = cv.GetTextSize(label, self.font)
        print ((size[0] + w) / 2, (size[1] - h) / 2)
        cv.PutText(dst, label, ((size[0] - w) / 2, (size[1] + h) / 2), self.font, (255,255,255))

    def redraw_stereo(self, lscrib, rscrib, lrgb, rrgb):
        display = cv.CreateMat(self.height, 2 * self.width + 100, cv.CV_8UC3)
        cv.Copy(lscrib, cv.GetSubRect(display, (0,0,self.width,self.height)))
        cv.Copy(rscrib, cv.GetSubRect(display, (self.width,0,self.width,self.height)))
        cv.Set(cv.GetSubRect(display, (2 * self.width,0,100,self.height)), (255, 255, 255))
        #cv.Resize(self.button, cv.GetSubRect(display, (2 * self.width,380,100,100)))
        self.button(cv.GetSubRect(display, (2 * self.width,380,100,100)))

        if not self.calibrated:
            if len(self.db) != 0:
                # Report dimensions of the n-polytope
                Ps = [v[0] for v in self.db.values()]
                Pmins = reduce(lmin, Ps)
                Pmaxs = reduce(lmax, Ps)
                ranges = [(x-n) for (x, n) in zip(Pmaxs, Pmins)]
                for i, (label, lo, hi) in enumerate(zip(["X", "Y", "Size"], Pmins, Pmaxs)):
                    y = 100 + 100 * i
                    (width,_),_ = cv.GetTextSize(label, self.font)
                    cv.PutText(display, label, (2 * self.width + (100 - width) / 2, 100 + 100 * i), self.font, (0,0,0))
                    cv.Line(display,
                            (2 * self.width + lo * 100, y + 20),
                            (2 * self.width + hi * 100, y + 20),
                            (0,0,0),
                            4)
        else:
            cv.PutText(display, "acc.", (2 * self.width, 50), self.font, (0,0,0))
            epierror = self.sc.epipolar1(lrgb, rrgb)
            if epierror == -1:
                msg = "?"
            else:
                msg = "%.2f" % epierror
            cv.PutText(display, msg, (2 * self.width, 150), self.font, (0,0,0))
            if epierror != -1:
                cv.PutText(display, "dim", (2 * self.width, 250), self.font, (0,0,0))
                dim = self.sc.chessboard_size(lrgb, rrgb)
                cv.PutText(display, "%.3f" % dim, (2 * self.width, 350), self.font, (0,0,0))

        cv.ShowImage("display", display)
        k = cv.WaitKey(6)

def main():
    from optparse import OptionParser
    rospy.init_node('calibrationnode')
    parser = OptionParser()
    parser.add_option("-w", "--web", dest="web", action="store_true", default=False, help="create backend for web-based calibration")
    parser.add_option("-o", "--opencv", dest="web", action="store_false", help="use OpenCV-based GUI for calibration (default)")
    options, args = parser.parse_args()
    if options.web:
        node = WebCalibrationNode()
    else:
        node = OpenCVCalibrationNode()
    rospy.spin()

if __name__ == "__main__":
    main()
