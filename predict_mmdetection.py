#!/usr/bin/env python
#
# This file uses MMdetection for instance segmentation.
# It is one of the official baselines for the Food Recognition benchmark 2022 challenge.
#
# NOTE: MMdetection needs the model and **its** aicrowd.json file to be submitted along with your code.
#
# Making submission using Detectron2:
# 1. Copy the aicrowd_mmdetection_example.json from utils to home directory:
#    #> cp utils/aicrowd_mmdetection_example.json aicrowd.json
# 2. Change the model in `predict.py` to MMDetectionPredictor.
# 3. Add your pre-trained model into the folder `./models`.
# 4. Submit your code using git-lfs
#    #> git lfs install
#    #> git lfs track "*.pth"
#    #> git add .gitattributes
#    #> git add models
#

import os
import json
import glob
from PIL import Image
import importlib
import numpy as np
import cv2
import torch
import traceback
import pickle
import shutil
import glob
import tempfile
import time
import mmcv
import torch.distributed as dist
from mmcv.image import tensor2imgs
from mmdet.core import encode_mask_results
from mmcv import Config, DictAction
from mmcv.cnn import fuse_conv_bn
from mmcv.parallel import MMDataParallel, MMDistributedDataParallel
from mmcv.runner import get_dist_info, init_dist, load_checkpoint, wrap_fp16_model
from mmdet.apis import init_detector, inference_detector

from mmdet.apis import multi_gpu_test
from mmdet.datasets import build_dataloader, build_dataset, replace_ImageToTensor
from mmdet.models import build_detector
import pycocotools.mask as mask_util

from utils.mmdet_inference import inference
from evaluator.food_challenge import FoodChallengePredictor


"""
Expected ENVIRONMENT Variables
* AICROWD_TEST_IMAGES_PATH : abs path to  folder containing all the test images
* AICROWD_PREDICTIONS_OUTPUT_PATH : path where you are supposed to write the output predictions.json
"""


class MMDetectionPredictor(FoodChallengePredictor):

    """
    PARTICIPANT_TODO:
    You can do any preprocessing required for your codebase here like loading up models into memory, etc.
    """

    def prediction_setup(self):
        # self.PADDING = 50
        # self.SEGMENTATION_LENGTH = 10
        # self.MAX_NUMBER_OF_ANNOTATIONS = 10

        # set the config parameters, including the architecture which was previously used
        self.cfg_name, self.checkpoint_name = self.get_mmdetection_config()
        self.cfg = Config.fromfile(self.cfg_name)
        # self.test_img_path = os.getenv("AICROWD_TEST_IMAGES_PATH", os.getcwd() + "/data/images/")
        self.test_predictions_file = self.create_test_predictions(self.test_data_path)

        if self.cfg.get("cudnn_benchmark", False):
            torch.backends.cudnn.benchmark = True
        self.cfg.data.samples_per_gpu = 1
        self.cfg.data.workers_per_gpu = 2
        self.cfg.model.pretrained = None
        self.cfg.data.test.test_mode = True
        self.cfg.data.test.ann_file = self.test_predictions_file.name
        self.cfg.data.test.img_prefix = self.test_data_path

        self.model = init_detector(self.cfg_name, self.checkpoint_name, device="cuda:0")

        fp16_cfg = self.cfg.get("fp16", None)
        if fp16_cfg is not None:
            wrap_fp16_model(self.model)

        # Load annotations
        with open(self.test_predictions_file.name) as f:
            self.annotations = json.loads(f.read())
        self.cat_ids = [category["id"] for category in self.annotations["categories"]]

        self.model.CLASSES = [
            category["name"] for category in self.annotations["categories"]
        ]

    """
    PARTICIPANT_TODO:
    During the evaluation all image file path will be provided one by one.
    NOTE: In case you want to load your model, please do so in `predict_setup` function.
    """

    def prediction(self, image_path):
        print("Generating for", image_path)
        # read the image
        result = inference(self.model, image_path)
        # RLE Encode the masks
        result = (result[0], encode_mask_results(result[1]))
        result = self.segm2jsonformat(result, image_path)
        return result

    def xyxy2xywh(self, bbox):
        _bbox = bbox.tolist()
        return [
            _bbox[0],
            _bbox[1],
            _bbox[2] - _bbox[0] + 1,
            _bbox[3] - _bbox[1] + 1,
        ]

    def segm2jsonformat(self, result, image_path):
        segm_json_results = []
        img_id = int(os.path.basename(image_path).split(".")[0])
        det, seg = result
        # print("image:",img_id)
        for label in range(len(det)):
            bboxes = det[label]
            # print(type(bboxes))
            segms = seg[label]
            mask_score = [bbox[4] for bbox in bboxes]
            for i in range(len(bboxes)):
                data = dict()
                data["image_id"] = img_id
                data["bbox"] = self.xyxy2xywh(bboxes[i])
                data["score"] = float(mask_score[i])
                data["category_id"] = self.cat_ids[label]
                if isinstance(segms[i]["counts"], bytes):
                    segms[i]["counts"] = segms[i]["counts"].decode()
                data["segmentation"] = segms[i]
                segm_json_results.append(data)
        return segm_json_results

    def create_test_predictions(self, images_path):
        test_predictions_file = tempfile.NamedTemporaryFile(mode="w+", suffix=".json")
        annotations = {"categories": [], "info": {}, "images": []}
        for item in glob.glob(images_path + "/*.jpg"):
            image_dict = dict()
            img = mmcv.imread(item)
            height, width, __ = img.shape
            id = int(os.path.basename(item).split(".")[0])
            image_dict["image_id"] = id
            image_dict["file_name"] = os.path.basename(item)
            image_dict["width"] = width
            image_dict["height"] = height
            annotations["images"].append(image_dict)
        annotations["categories"] = json.loads(open("utils/classes.json").read())
        json.dump(annotations, open(test_predictions_file.name, "w"))

        return test_predictions_file

    def get_mmdetection_config(self):
        with open("aicrowd.json") as f:
            content = json.load(f)
            config_fname = content["model_config_file"]
            checkpoint_fname = content["model_path"]
        # config = Config.fromfile(config_fname)
        return (config_fname, checkpoint_fname)


if __name__ == "__main__":
    submission = MMDetectionPredictor()
    submission.run()
    print("Successfully generated predictions!")
