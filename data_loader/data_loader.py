# Copyright 2018 Jaewook Kang (jwkang10@gmail.com) All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
# -*- coding: utf-8 -*-

"""Efficient tf-tiny-pose-estimation using tf.data.Dataset.
    code ref: https://github.com/edvardHua/PoseEstimationForMobile
"""

from __future__ import absolute_import, division, print_function

import sys

import tensorflow as tf
import os

from pycocotools.coco import COCO

# for coco dataset
from data_loader import dataset_augment
from data_loader.dataset_prepare import CocoMetadata

class DataLoader(object):
    """Generates DataSet input_fn for training or evaluation
        Args:
            is_training: `bool` for whether the input is for training
            data_dir:   `str` for the directory of the training and validation data;
                            if 'null' (the literal string 'null', not None), then construct a null
                            pipeline, consisting of empty images.
            use_bfloat16: If True, use bfloat16 precision; else use float32.
            transpose_input: 'bool' for whether to use the double transpose trick
    """

    def __init__(self,
                 train_config,
                 model_config,
                 preproc_config,
                 images_dir_path,
                 annotation_json_path,
                 transpose_input=False,
                 use_bfloat16=False):

        self.image_preprocessing_fn = dataset_augment.preprocess_image
        self.use_bfloat16 = use_bfloat16
        self.images_dir_path = images_dir_path
        self.annotation_json_path = annotation_json_path
        self.annotations_info = None
        self.train_config = train_config
        self.model_config = model_config
        self.preproc_config = preproc_config

        if images_dir_path == 'null' or images_dir_path == '' or images_dir_path is None:
            exit(1)
        if annotation_json_path == 'null' or annotation_json_path == '' or annotation_json_path is None:
            exit(1)

        self.transpose_input = transpose_input

        self.annotations_info = COCO(self.annotation_json_path)

        self.imgIds = self.annotations_info.getImgIds()

    def _set_shapes(self, img, heatmap):

        batch_size = self.train_config.batch_size

        img.set_shape([batch_size,
                       self.model_config.input_size,
                       self.model_config.input_size,
                       self.model_config.input_chnum])

        heatmap.set_shape([batch_size,
                           self.model_config.output_size,
                           self.model_config.output_size,
                           self.model_config.output_chnum])
        return img, heatmap

    def _parse_function(self, imgId, ann=None):
        """
        :param imgId: Tensor
        :return:
        """
        try:
            imgId = imgId.numpy()
        except AttributeError:
            # print(AttributeError)
            var = None

        if ann is not None:
            self.annotations_info = ann

        image_info = self.annotations_info.loadImgs([imgId])[0]
        keypoint_info_ids = self.annotations_info.getAnnIds(imgIds=imgId)
        keypoint_infos = self.annotations_info.loadAnns(keypoint_info_ids)
        image_id = image_info['id']

        img_filename = image_info['file_name']
        image_filepath = os.path.join(self.images_dir_path, img_filename)

        img_meta_data = CocoMetadata(idx=image_id,
                                     img_path=image_filepath,
                                     img_meta=image_info,
                                     annotations=keypoint_infos,
                                     sigma=self.preproc_config.heatmap_std)

        # print('joint_list = %s' % img_meta_data.joint_list)
        images, labels = self.image_preprocessing_fn(img_meta_data=img_meta_data,
                                                     preproc_config=self.preproc_config)

        return images, labels

    def input_fn(self, params=None):
        """Input function which provides a single batch for train or eval.
            Args:
                params: `dict` of parameters passed from the `TPUEstimator`.
                  `params['batch_size']` is always provided and should be used as the
                  effective batch size.
            Returns:
                A `tf.data.Dataset` object.
            doc reference: https://www.tensorflow.org/api_docs/python/tf/data/TFRecordDataset
        """

        dataset = tf.data.Dataset.from_tensor_slices(self.imgIds)
        dataset = dataset.apply(tf.data.experimental.map_and_batch(
            map_func=lambda imgId: tuple(
                tf.py_function(
                    func=self._parse_function,
                    inp=[imgId],
                    Tout=[tf.float32, tf.float32])),
            batch_size=self.train_config.batch_size,
            num_parallel_calls=self.train_config.multiprocessing_num,
            drop_remainder=True))

        # cache entire dataset in memory after preprocessing
        # dataset = dataset.cache() # do not use this code for OOM problem

        dataset = dataset.map(self._set_shapes,
                              num_parallel_calls=self.train_config.multiprocessing_num)

        # Prefetch overlaps in-feed with training
        # dataset = dataset.prefetch(tf.data.experimental.AUTOTUNE) # tf.data.experimental.AUTOTUNE have to be upper than 1.13
        dataset = dataset.prefetch(buffer_size=self.train_config.batch_size * 3)
        # tf.logging.info('[Input_fn] dataset pipeline building complete')

        return dataset

    def get_images(self, idx, batch_size):
        imgs = []
        labels = []
        for i in range(batch_size):
            img, label = self._parse_function(self.imgIds[i + idx])
            imgs.append(img)
            labels.append(label)
        import numpy as np
        return np.array(imgs), np.array(labels)
