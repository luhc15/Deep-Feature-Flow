# --------------------------------------------------------
# Deep Feature Flow
# Copyright (c) 2017 Microsoft
# Licensed under The Apache-2.0 License [see LICENSE for details]
# Written by Xizhou Zhu, Yi Li, Haochen Zhang
# --------------------------------------------------------

import _init_paths

import argparse
import os
import glob
import sys
import logging
import pprint
import cv2
from config.config import config, update_config
from utils.image import resize, transform
from PIL import Image
import numpy as np
import re
import pickle

# get config
os.environ['PYTHONUNBUFFERED'] = '1'
os.environ['MXNET_CUDNN_AUTOTUNE_DEFAULT'] = '0'
os.environ['MXNET_ENABLE_GPU_P2P'] = '0'
cur_path = os.path.abspath(os.path.dirname(__file__))
update_config(cur_path + '/../experiments/dff_deeplab/cfgs/dff_deeplab_vid_demo.yaml')

sys.path.insert(0, os.path.join(cur_path, '../external/mxnet', config.MXNET_VERSION))
import mxnet as mx
from core.tester import im_segment, Predictor
from symbols import *
from utils.load_model import load_param_multi
from utils.show_boxes import show_boxes, draw_boxes
from utils.tictoc import tic, toc
from nms.nms import py_nms_wrapper, cpu_nms_wrapper, gpu_nms_wrapper

def parse_args():
    parser = argparse.ArgumentParser(description='Show Deep Feature Flow demo')
    parser.add_argument('-i', '--interval', type=int, default=1)
    parser.add_argument('-e', '--num_ex', type=int, default=10)
    parser.add_argument('-s', '--start_num', type=int, default=0)
    args = parser.parse_args()
    return args

args = parse_args()

def fast_hist(pred, label, n):
    k = (label >= 0) & (label < n)
    return np.bincount(
        n * label[k].astype(int) + pred[k], minlength=n ** 2).reshape(n, n)

def per_class_iu(hist):
    ius = np.true_divide(np.diag(hist), (hist.sum(1) + hist.sum(0) - np.diag(hist)))
    ius = np.delete(ius, [3, 6, 9, 14, 15, 16, 17, 18])
    return ius

def getpallete(num_cls):
    """
    this function is to get the colormap for visualizing the segmentation mask
    :param num_cls: the number of visulized class
    :return: the pallete
    """
    n = num_cls
    pallete_raw = np.zeros((n, 3)).astype('uint8')
    pallete = np.zeros((n, 3)).astype('uint8')

    pallete_raw[6, :] =  [111,  74,   0]
    pallete_raw[7, :] =  [ 81,   0,  81]
    pallete_raw[8, :] =  [128,  64, 128] # [128,  64, 128]  # Road
    pallete_raw[9, :] =  [  0,   0, 192] # [244,  35, 232]  # Sidewalk
    pallete_raw[10, :] =  [250, 170, 160]
    pallete_raw[11, :] = [230, 150, 140]
    pallete_raw[12, :] = [128,   0,   0] # [ 70,  70,  70]  # Building
    pallete_raw[13, :] = [ 64, 192,   0] # [102, 102, 156]  # Wall
    pallete_raw[14, :] = [64,   64, 128] # [190, 153, 153]  # Fence
    pallete_raw[15, :] = [180, 165, 180]
    pallete_raw[16, :] = [150, 100, 100]
    pallete_raw[17, :] = [150, 120,  90]
    pallete_raw[18, :] = [192, 192, 128] # [153, 153, 153]  # Pole
    pallete_raw[19, :] = [153, 153, 153]
    pallete_raw[20, :] = [  0,  64,  64] # [250, 170,  30]  # Traffic Light
    pallete_raw[21, :] = [192, 128, 128] # [220, 220,   0]  # Traffic Sign
    pallete_raw[22, :] = [128, 128,   0] # [107, 142,  35]  # Tree / Vegetation
    pallete_raw[23, :] = [  0, 128,   0] # [152, 251, 152]  # Grass / Terrain
    pallete_raw[24, :] = [128, 128, 128] # [ 70, 130, 180]  # Sky
    pallete_raw[25, :] = [64,   64,   0] # [220,  20,  60]  # Person
    pallete_raw[26, :] = [  0, 128, 192] # [255,   0,   0]  # Rider
    pallete_raw[27, :] = [ 64,   0, 128] # [  0,   0, 142]  # Car
    pallete_raw[28, :] = [ 64, 128, 192] # [  0,   0,  70]  # Truck
    pallete_raw[29, :] = [192, 128, 192] # [  0,  60, 100]  # Bus
    pallete_raw[30, :] = [  0,   0,  90]
    pallete_raw[31, :] = [  0,   0, 110]
    pallete_raw[32, :] = [192,  64, 128] # [  0,  80, 100]  # Train
    pallete_raw[33, :] = [192,   0, 192] # [  0,   0, 230]  # Motorcycle
    pallete_raw[34, :] = [  0,   0,   0] # [119,  11,  32]  # Bicycle

    train2regular = [7, 8, 11, 12, 13, 17, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 31, 32, 33]

    for i in range(len(train2regular)):
        pallete[i, :] = pallete_raw[train2regular[i]+1, :]

    pallete = pallete.reshape(-1)

    return pallete

codes = {                   # CamVid                # Cityscapes
    (128,  64, 128): 0,     # Road                  Road
    (  0,   0, 192): 1,     # Sidewalk              Sidewalk
    (128,   0,   0): 2,     # Building              Building
    ( 64, 192,   0): 255,   # Wall                  Wall
    ( 64,  64, 128): 4,     # Fence                 Fence
    (192, 192, 128): 5,     # Column_Pole           Pole
    (  0,  64,  64): 255,   # TrafficLight          Traffic Light
    (192, 128, 128): 7,     # SignSymbol            Traffic Sign
    (128, 128,   0): 8,     # Tree                  Vegetation
    (128, 128, 128): 10,    # Sky                   Sky
    ( 64,  64,   0): 11,    # Pedestrian            Person
    (  0, 128, 192): 12,    # Bicyclist             Rider
    ( 64,   0, 128): 13,    # Car                   Car
    ( 64, 128, 192): 255,   # SUVPickupTruck        Truck
    (192, 128, 192): 255,   # Truck_Bus             Bus
    (192,  64, 128): 255,   # Train                 Train
    (192,   0, 192): 255,   # MotorcycleScooter     Motorcycle
    (  0,   0,   0): 255,   # Void                  Void

    (192, 192,   0): 255,   # VegetationMisc        Vegetation
    (192, 128,  64): 255,   # Child                 Person

    (  0, 128,  64): 255,   # Bridge                Bridge
    ( 64,   0,  64): 255,   # Tunnel                Tunnel

    (192,   0, 128): 255,   # Archway
    ( 64, 192, 128): 255,   # ParkingBlock
    (  0,   0,  64): 255,   # TrafficCone
    (128, 128,  64): 255,   # Misc_Text
    (128,   0, 192): 255,   # LaneMkgsDriv
    (128, 128, 192): 255,   # RoadShoulder
    (192,   0,  64): 255,   # LaneMkgsNonDriv
    ( 64, 128,  64): 255,   # Animal
    ( 64,   0, 192): 255,   # CartLuggagePram
    (128,  64,  64): 255,   # OtherMoving
}

def quantize(label, codes):
    result = np.ndarray(shape=label.shape[:2], dtype=int)
    result[:, :] = 255
    for rgb, idx in codes.items():
        result[(label==rgb).all(2)] = idx
    return result

def main():
    # get symbol
    pprint.pprint(config)
    config.symbol = 'resnet_v1_101_flownet_deeplab'
    model1 = '/../model/rfcn_dff_flownet_vid'
    model2 = '/../model/deeplab_dcn_camvid'
    sym_instance = eval(config.symbol + '.' + config.symbol)()
    key_sym = sym_instance.get_key_test_symbol(config)
    next_key_sym = sym_instance.get_key_test_symbol(config)
    cur_sym = sym_instance.get_cur_test_symbol(config)

    # settings
    num_classes = 19
    snip_len = 30
    interv = args.interval
    num_ex = args.num_ex
    start_num = args.start_num

    # load demo data
    set1_images = sorted(glob.glob(cur_path + '/../data/CamVid/data-Seq05VD/*.png'))
    set1_images = set1_images[snip_len * start_num :]
    set2_images = sorted(glob.glob(cur_path + '/../data/CamVid/data-0001TP_2/*.png'))

    label_files  = sorted(glob.glob(cur_path + '/../data/CamVid/labels/val/Seq05VD/*.png'))[1: ]
    label_files += sorted(glob.glob(cur_path + '/../data/CamVid/labels/val/0001TP/*.png'))[1: ]
    label_files = label_files[start_num :]

    output_dir = cur_path + '/../demo/deeplab_dff/'
    mv_files = [cur_path + '/../data/CamVid/camvid_Seq05VD.pkl',
        cur_path + '/../data/CamVid/camvid_0001TP_2.pkl']
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    key_frame_interval = interv

    #
    lb_pos = 0
    set1_ex = 171
    set2_ex = 62
    image_names_trunc = []
    for i in range(1, min(num_ex, set1_ex - start_num)):
        snip_pos = i * snip_len
        offset = (interv + (i % 2)) / 2 # i % interv
        start_pos = lb_pos - offset
        image_names_trunc.extend(set1_images[snip_pos + start_pos : snip_pos + start_pos + interv + 1])
    for j in range(1, min(num_ex - (i+1), set2_ex)):
        snip_pos = j * snip_len
        offset = (interv + (i % 2)) / 2 # j % interv
        start_pos = lb_pos - offset
        image_names_trunc.extend(set2_images[snip_pos + start_pos : snip_pos + start_pos + interv + 1])
    image_names = image_names_trunc
    # print 'len', len(image_names)

    for idx, mv_file in enumerate(mv_files):
        print 'mv file:', mv_file
        mv_cam = pickle.load(open(mv_file, 'rb'))
        mv_cam = np.transpose(mv_cam, (0, 3, 1, 2))
        if idx == 0:
            mvs = mv_cam
        else:
            mvs = np.concatenate((mvs, mv_cam), axis=0)
        print "mvs.shape %s" % (mvs.shape,)

    # test params
    time = 0
    count = 0
    hist = np.zeros((num_classes, num_classes))
    lb_idx = 0

    all_imgs = set1_images + set2_images

    for snip_idx in range(len(image_names) / (interv + 1)):

        snip_names = image_names[snip_idx * (interv + 1) : (snip_idx + 1) * (interv + 1)]

        data = []
        mv_tensor = None

        print '\n\nsnippet', snip_idx
        for idx, im_name in enumerate(snip_names):
            assert os.path.exists(im_name), ('%s does not exist'.format(im_name))
            im = cv2.imread(im_name, cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
            target_size = config.SCALES[0][0]
            max_size = config.SCALES[0][1]
            # im, im_scale = resize(im, target_size, max_size, stride=config.network.IMAGE_STRIDE)
            im_scale = 1.
            im_tensor = transform(im, config.network.PIXEL_MEANS)
            im_info = np.array([[im_tensor.shape[2], im_tensor.shape[3], im_scale]], dtype=np.float32)
            mv_idx = all_imgs.index(im_name)
            mv_tensor = np.expand_dims(mvs[mv_idx], axis=0) / 16.
            data.append({
                'data': im_tensor,
                'im_info': im_info,
                'm_vec': mv_tensor,
                'feat_forw': np.zeros((1,config.network.DFF_FEAT_DIM,1,1)),
                'feat_back': np.zeros((1,config.network.DFF_FEAT_DIM,1,1)),
            })


        # get predictor
        data_names = ['data', 'm_vec', 'feat_forw', 'feat_back']
        label_names = []
        data = [[mx.nd.array(data[i][name]) for name in data_names] for i in xrange(len(data))]
        max_data_shape = [[('data', (1, 3, max([v[0] for v in config.SCALES]), max([v[1] for v in config.SCALES])))]]
        provide_data = [[(k, v.shape) for k, v in zip(data_names, data[i])] for i in xrange(len(data))]
        provide_label = [None for i in xrange(len(data))]
        # models: rfcn_dff_flownet_vid, deeplab_cityscapes
        arg_params, aux_params = load_param_multi(cur_path + model1, cur_path + model2, 0, process=True)
        key_predictor = Predictor(key_sym, data_names, label_names,
                              context=[mx.gpu(0)], max_data_shapes=max_data_shape,
                              provide_data=provide_data, provide_label=provide_label,
                              arg_params=arg_params, aux_params=aux_params)
        next_key_predictor = Predictor(next_key_sym, data_names, label_names,
                              context=[mx.gpu(0)], max_data_shapes=max_data_shape,
                              provide_data=provide_data, provide_label=provide_label,
                              arg_params=arg_params, aux_params=aux_params)
        cur_predictor = Predictor(cur_sym, data_names, label_names,
                              context=[mx.gpu(0)], max_data_shapes=max_data_shape,
                              provide_data=provide_data, provide_label=provide_label,
                              arg_params=arg_params, aux_params=aux_params)
        nms = gpu_nms_wrapper(config.TEST.NMS, 0)

        # # warm up
        # for j in xrange(min(interv, 2)):
        #     data_batch = mx.io.DataBatch(data=[data[j]], label=[], pad=0, index=0,
        #                                  provide_data=[[(k, v.shape) for k, v in zip(data_names, data[j])]],
        #                                  provide_label=[None])
        #     # load next keyframe data
        #     if j % (key_frame_interval + 1) == 0:
        #         assert (j + key_frame_interval < len(snip_names))
        #         next_idx = j + key_frame_interval
        #         data_batch_next = mx.io.DataBatch(data=[data[next_idx]], label=[], pad=0, index=next_idx,
        #                                           provide_data=[[(k, v.shape) for k, v in zip(data_names, data[next_idx])]],
        #                                           provide_label=[None])
        #     # scales = [data_batch.data[i][1].asnumpy()[0, 2] for i in xrange(len(data_batch.data))]
        #     if j % (key_frame_interval + 1) == 0:
        #         # scores, boxes, data_dict, feat = im_detect(key_predictor, data_batch, data_names, scales, config)
        #         output_all, feat = im_segment(key_predictor, data_batch)
        #         output_all = [mx.ndarray.argmax(output['croped_score_output'], axis=1).asnumpy() for output in output_all]

        #         _, feat_next = im_segment(next_key_predictor, data_batch_next)
        #     else:
        #         data_batch.data[0][-2] = feat
        #         data_batch.provide_data[0][-2] = ('feat_forw', feat.shape)
        #         data_batch.data[0][-1] = feat_next
        #         data_batch.provide_data[0][-1] = ('feat_back', feat_next.shape)
        #         # scores, boxes, data_dict, _ = im_detect(cur_predictor, data_batch, data_names, scales, config)
        #         output_all, _ = im_segment(cur_predictor, data_batch)
        #         output_all = [mx.ndarray.argmax(output['croped_score_output'], axis=1).asnumpy() for output in output_all]

        print "warmup done"

        for idx, im_name in enumerate(snip_names):
            data_batch = mx.io.DataBatch(data=[data[idx]], label=[], pad=0, index=idx,
                                         provide_data=[[(k, v.shape) for k, v in zip(data_names, data[idx])]],
                                         provide_label=[None])
            # scales = [data_batch.data[i][1].asnumpy()[0, 2] for i in xrange(len(data_batch.data))]

            # load next keyframe data
            if idx % (key_frame_interval + 1) == 0:
                assert (idx + key_frame_interval < len(snip_names))
                next_idx = idx + key_frame_interval
                data_batch_next = mx.io.DataBatch(data=[data[next_idx]], label=[], pad=0, index=next_idx,
                                                  provide_data=[[(k, v.shape) for k, v in zip(data_names, data[next_idx])]],
                                                  provide_label=[None])

            tic()
            if idx % (key_frame_interval + 1) == 0:
                print '\nframe {} (key)     next {}'.format(idx, next_idx)
                # scores, boxes, data_dict, feat = im_detect(key_predictor, data_batch, data_names, scales, config)
                output_all, feat = im_segment(key_predictor, data_batch)
                output_all = [mx.ndarray.argmax(output['croped_score_output'], axis=1).asnumpy() for output in output_all]

                _, feat_next = im_segment(next_key_predictor, data_batch_next)

                forw_warp = [feat]
                for i in range(key_frame_interval):
                    feat_sym = mx.sym.Variable(name="feat")
                    m_vec_sym = mx.sym.Variable(name="m_vec")

                    m_vec_grid = mx.sym.GridGenerator(data=m_vec_sym, transform_type='warp', name='m_vec_grid')
                    feat_warp = mx.sym.BilinearSampler(data=feat_sym, grid=m_vec_grid, name='warping_feat')

                    m_vec_data = mx.ndarray.negative(data[idx + 1 + i][1])
                    f_exec = feat_warp.bind(ctx=mx.gpu(),
                        args={"feat": forw_warp[-1], "m_vec": m_vec_data},
                        group2ctx={"feat": mx.gpu(), "m_vec": mx.cpu()})
                    f_exec.forward()

                    forw_warp.append(f_exec.outputs[0])

                for i in range(len(forw_warp)):
                    weight = (1. * key_frame_interval - i) / key_frame_interval
                    forw_warp[i] = weight * forw_warp[i]

                # print 'forw_warp: ', len(forw_warp)

                back_warp = [feat_next]
                for i in range(key_frame_interval):
                    feat_sym = mx.sym.Variable(name="feat")
                    m_vec_sym = mx.sym.Variable(name="m_vec")

                    m_vec_grid = mx.sym.GridGenerator(data=m_vec_sym, transform_type='warp', name='m_vec_grid')
                    feat_warp = mx.sym.BilinearSampler(data=feat_sym, grid=m_vec_grid, name='warping_feat')

                    m_vec_data = data[idx + (key_frame_interval - i)][1]
                    b_exec = feat_warp.bind(ctx=mx.gpu(),
                        args={"feat": back_warp[-1], "m_vec": m_vec_data},
                        group2ctx={"feat": mx.gpu(), "m_vec": mx.cpu()})
                    b_exec.forward()

                    back_warp.append(b_exec.outputs[0])

                for i in range(len(back_warp)):
                    weight = (1. * key_frame_interval - i) / key_frame_interval
                    back_warp[i] = weight * back_warp[i]

                back_warp.reverse()
                # print 'back_warp: ', len(back_warp)

            elif idx % (key_frame_interval + 1) == key_frame_interval:
                continue

            else:
                print '\nframe {} (intermediate)'.format(idx)
                # print 'modulo {}'.format(idx % key_frame_interval)
                feat_forw = forw_warp[idx % (key_frame_interval + 1)]
                feat_back = back_warp[idx % (key_frame_interval + 1)]

                data_batch.data[0][-2] = feat_forw
                data_batch.provide_data[0][-2] = ('feat_forw', feat_forw.shape)
                data_batch.data[0][-1] = feat_back
                data_batch.provide_data[0][-1] = ('feat_back', feat_back.shape)
                # scores, boxes, data_dict, _ = im_detect(cur_predictor, data_batch, data_names, scales, config)
                output_all, _ = im_segment(cur_predictor, data_batch)
                output_all = [mx.ndarray.argmax(output['croped_score_output'], axis=1).asnumpy() for output in output_all]

            elapsed = toc()
            time += elapsed
            count += 1
            print 'testing {} {:.4f}s [{:.4f}s]'.format(im_name, elapsed, time/count)

            pred = np.uint8(np.squeeze(output_all))
            segmentation_result = Image.fromarray(pred)
            pallete = getpallete(256)
            segmentation_result.putpalette(pallete)
            _, im_filename = os.path.split(im_name)
            segmentation_result.save(output_dir + '/seg_' + im_filename)

            label = None

            _, lb_filename = os.path.split(label_files[lb_idx])
            im_comps = re.split('[_.]', im_filename)
            lb_comps = re.split('[_.]', lb_filename)
            # if annotation available for frame
            if im_comps[0] == lb_comps[0] and im_comps[1] == lb_comps[1]:
                print 'label {}'.format(lb_filename)
                label = np.asarray(Image.open(label_files[lb_idx]))
                label = quantize(label, codes)
                if lb_idx < len(label_files) - 1:
                    lb_idx += 1

            if label is not None:
                curr_hist = fast_hist(pred.flatten(), label.flatten(), num_classes)
                hist += curr_hist
                print 'mIoU {mIoU:.3f}'.format(
                    mIoU=round(np.nanmean(per_class_iu(curr_hist)) * 100, 2))
                print '(cum) mIoU {mIoU:.3f}'.format(
                    mIoU=round(np.nanmean(per_class_iu(hist)) * 100, 2))

    ious = per_class_iu(hist) * 100
    print ' '.join('{:.03f}'.format(i) for i in ious)
    print '===> final mIoU {mIoU:.3f}'.format(mIoU=round(np.nanmean(ious), 2))

    print 'done'

if __name__ == '__main__':
    main()
