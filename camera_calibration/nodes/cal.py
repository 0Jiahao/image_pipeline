#!/usr/bin/python

PKG = 'camera_calibration' # this package name
import roslib; roslib.load_manifest(PKG)

import rospy
import sensor_msgs.msg
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
        scrib = rgb

        if not self.calibrated:
            (ok, corners) = get_corners(rgb, refine = False)
            if ok:
                # Compute some parameters for this chessboard
                Xs = [x for (x, y) in corners]
                Ys = [y for (x, y) in corners]
                p_x = mean(Xs) / 640
                p_y = mean(Ys) / 480
                p_size = (max(Xs) - min(Xs)) / 640
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

                scrib = cv.CloneMat(rgb)
                for (co, im) in [(corners, scrib)]:
                    src = cv.Reshape(mk_image_points([co]), 2)
                    cv.DrawChessboardCorners(im, (8, 6), cvmat_iterator(src), True)

                # If the image is a min or max in every parameter, add to the collection
                if any(is_min) or any(is_max):
                    self.db[str(is_min + is_max)] = (params, rgb)
        else:
            scrib = self.mc.remap(rgb)

        self.redraw_monocular(scrib, rgb)

    def handle_stereo(self, lmsg, rmsg):

        lrgb = self.mkgray(lmsg)
        rrgb = self.mkgray(rmsg)
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
                    p_x = mean(Xs) / 640
                    p_y = mean(Ys) / 480
                    p_size = (max(Xs) - min(Xs)) / 640
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

class OpenCVCalibrationNode(CalibrationNode):
    """ Calibration node with an OpenCV Gui """

    def __init__(self):

        CalibrationNode.__init__(self)
        cv.NamedWindow("display")
        self.font = cv.InitFont(cv.CV_FONT_HERSHEY_SIMPLEX, 1, 1, thickness = 2, line_type = cv.CV_AA)
        self.button = cv.LoadImage("%s/button.jpg" % roslib.packages.get_pkg_dir(PKG))
        cv.SetMouseCallback("display", self.on_mouse)

    def on_mouse(self, event, x, y, flags, param):
        if event == cv.CV_EVENT_LBUTTONDOWN:
            vv = list(self.db.values())
            # vv is a list of pairs (p, i) for monocular, and triples (p, l, r) for stereo
            if len(vv[0]) == 2:
                images = [i for (p, i) in vv]
                self.mc.cal(images)
                self.calibrated = True
                self.mc.report()
                self.mc.ost()
            else:
                limages = [ l for (p, l, r) in vv ]
                rimages = [ r for (p, l, r) in vv ]
                self.sc.cal(limages, rimages)
                self.calibrated = True
                #for (i, (p, limg, rimg)) in enumerate(self.db.values()):
                #    cv.SaveImage("/tmp/cal%04d.png" % i, self.sc.lremap(limg))

                self.sc.report()
                self.sc.ost()

    def redraw_monocular(self, scrib, rgb):
        display = cv.CreateMat(480, 640 + 100, cv.CV_8UC3)
        cv.Copy(scrib, cv.GetSubRect(display, (0,0,640,480)))
        cv.Set(cv.GetSubRect(display, (640,0,100,480)), (255, 255, 255))
        cv.Resize(self.button, cv.GetSubRect(display, (640,380,100,100)))

        # Report dimensions of the n-polytope
        Ps = [v[0] for v in self.db.values()]
        Pmins = reduce(lmin, Ps)
        Pmaxs = reduce(lmax, Ps)
        ranges = [(x-n) for (x, n) in zip(Pmaxs, Pmins)]

        if not self.calibrated:
            for i, (label, lo, hi) in enumerate(zip(["X", "Y", "Size"], Pmins, Pmaxs)):
                y = 100 + 100 * i
                (width,_),_ = cv.GetTextSize(label, self.font)
                cv.PutText(display, label, (640 + (100 - width) / 2, 100 + 100 * i), self.font, (0,0,0))
                cv.Line(display,
                        (640 + lo * 100, y + 20),
                        (640 + hi * 100, y + 20),
                        (0,0,0),
                        4)
        else:
            cv.PutText(display, "acc.", (640, 100), self.font, (0,0,0))

        cv.ShowImage("display", display)
        k = cv.WaitKey(6)

    def redraw_stereo(self, lscrib, rscrib, lrgb, rrgb):
        display = cv.CreateMat(480, 1280 + 100, cv.CV_8UC3)
        cv.Copy(lscrib, cv.GetSubRect(display, (0,0,640,480)))
        cv.Copy(rscrib, cv.GetSubRect(display, (640,0,640,480)))
        cv.Set(cv.GetSubRect(display, (1280,0,100,480)), (255, 255, 255))
        cv.Resize(self.button, cv.GetSubRect(display, (1280,380,100,100)))


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
                    cv.PutText(display, label, (1280 + (100 - width) / 2, 100 + 100 * i), self.font, (0,0,0))
                    cv.Line(display,
                            (1280 + lo * 100, y + 20),
                            (1280 + hi * 100, y + 20),
                            (0,0,0),
                            4)
        else:
            cv.PutText(display, "acc.", (1280, 50), self.font, (0,0,0))
            epierror = self.sc.epipolar1(lrgb, rrgb)
            if epierror == -1:
                msg = "?"
            else:
                msg = "%.2f" % epierror
            cv.PutText(display, msg, (1280, 150), self.font, (0,0,0))
            if epierror != -1:
                cv.PutText(display, "dim", (1280, 250), self.font, (0,0,0))
                dim = self.sc.chessboard_size(lrgb, rrgb)
                cv.PutText(display, "%.3f" % dim, (1280, 350), self.font, (0,0,0))

        cv.ShowImage("display", display)
        k = cv.WaitKey(6)

rospy.init_node('calibrationnode')
node = OpenCVCalibrationNode()
rospy.spin()
