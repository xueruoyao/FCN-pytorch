"""
It is recommended that data augmentation is finished before training, which means
there wouldn't be any dynamic preprocessing in training process except normalisation.

We provide simple data augmentation code in utils/data_augmentation.py
TODO: add torch.jit.script
TODO: add tensorboard visualisation
TODO: add warm-up to optimizer
TODO: add valid dataset
"""

import datetime
import logging
import os.path
import random
import sys
import cv2
import numpy as np
from tqdm import tqdm
import torch
from torch import optim, nn
from args import TrainArgs, get_logger
import argparse
from utils import PNGTrainloader, PNGTestloader, SegmentationEvaluator
from ruamel import yaml
import shutil


def save_script(save_path):
    """ save all .py and .sh documents to save_path/code for reusing """
    shutil.copytree(os.getcwd(), os.path.join(save_path, "code"))
    for root, dirs, files in os.walk(save_path):
        for file in files:
            if file.split(".")[-1] not in ["py", "sh", "yml"]:
                os.remove(os.path.join(root, file))
        for d in dirs:
            if d in ["save", "__pycache__"]:
                shutil.rmtree(os.path.join(root, d))


@torch.no_grad()
def valider(train_args: argparse, logger):
    """ use train model to test valid data and give metrics """
    logger.info("validation")
    train_args.model.eval()
    test_dataloader = PNGTestloader(image_path=os.path.join(train_args.valid_data_path, "image"),
                                    chip_size=512, stride=256,
                                    n_class=train_args.n_class, batch_size=train_args.batch_size,
                                    device=train_args.device)
    evaluator = SegmentationEvaluator(true_label=range(train_args.n_class))

    max_batch_num = np.ceil(len(test_dataloader) / train_args.batch_size)
    last_batch_flag = False
    with tqdm(total=max_batch_num, unit_scale=True, unit=" batch", colour="cyan", ncols=60) as pbar:
        for i, (data, info) in enumerate(test_dataloader):
            data = data.to(train_args.device)  # data (batch_size, channels, height, width)
            preds = train_args.model(data)  # preds (batch_size, n_class, height, width)
            if i == (max_batch_num - 1):
                last_batch_flag = True
            for whole_label, image_name in test_dataloader.stitcher(preds, info, last_batch_flag):
                # before: whole label (n_class, height, width)
                whole_label = torch.argmax(whole_label, dim=0)
                # after: whole label (height, width)
                gt = torch.tensor(cv2.imread(os.path.join(train_args.valid_data_path, "gt", image_name))[:, :, 0])
                evaluator.accumulate(whole_label, gt.to(train_args.device))
            pbar.update()
    evaluator.log_metrics()


def trainer(train_args: argparse, logger):
    # 1. -------------Prepare dataloader, optimizer, scheduler, loss, evaluator---------------------------
    train_args.model.to(train_args.device)
    train_dataloader = PNGTrainloader(image_path=os.path.join(train_args.train_data_path, "image"),
                                      gt_path=os.path.join(train_args.train_data_path, "gt"),
                                      batch_size=train_args.batch_size, drop_last=True, shuffle=True)
    criterion = nn.CrossEntropyLoss()
    evaluator = SegmentationEvaluator(true_label=range(train_args.n_class))
    optimizer = optim.SGD(train_args.model.parameters(), lr=train_args.lr, momentum=0.9)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=5,
                                                     min_lr=1e-6, verbose=True)
    with open(os.path.join(Args.args.exp_train_path, "config.yml"), "a") as f:
        yaml.dump({"optimizer": {"name": "SGD", "state_dict": optimizer.state_dict()}}, f)
        f.write("\n")
        yaml.dump({"scheduler": {"name": "ReduceLROnPlateau", "state_dict": scheduler.state_dict()}}, f)
        f.write("\n")

    # 2. ---------------------------whether to load checkpoint-------------------------------------------
    start_epoch = 1  # range(start_epoch, epochs + 1) which works for loading checkpoint
    if train_args.check_point_mode == "load":
        checkpoint = torch.load(train_args.check_point_path)
        train_args.model.load_state_dict(checkpoint["model_state_dict"])
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        scheduler.load_state_dict(checkpoint["scheduler_state_dict"])
        start_epoch += checkpoint["epoch"]

    # 3. --------------------------Training Process and evaluation----------------------------------------
    train_args.model.train()
    batch_num = 0  # record for loss computation
    for epoch in range(start_epoch, train_args.epochs + 1):
        if (epoch - 1) % 5 == 0:
            logging.info(datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]"))
        loss_ = torch.tensor([0], dtype=torch.float32, device=train_args.device)
        with tqdm(total=int(len(train_dataloader) / train_args.batch_size),
                  unit_scale=True, unit=" batch", colour="cyan", ncols=60) as pbar:
            for i, (images, gts) in enumerate(train_dataloader):
                # images (batch_size, channel, height, width)
                # gts (batch_size, height, width)
                images = images.to(train_args.device)
                gts = gts.to(train_args.device)
                optimizer.zero_grad()
                # predictions (batch_size, n_class, height, width)
                predictions = train_args.model(images)
                loss = criterion(predictions, gts)
                loss_ += loss
                evaluator.accumulate(torch.argmax(predictions, dim=1), gts)
                loss.backward()
                optimizer.step()
                batch_num = i
                pbar.update(1)

        logger.info(f"epoch: {epoch}    loss: {round(loss_.item() / (batch_num + 1), 5)}")
        evaluator.log_metrics()
        evaluator.clear()
        scheduler.step(loss_)
        if epoch % 5 == 0:
            # validation
            valider(train_args, logger)
            train_args.model.train()

            # save model
            torch.save(train_args.model.state_dict(),
                       os.path.join(train_args.save_model_path, "model_epoch_{}.pth".format(epoch)))
            logger.info(f"epoch {epoch} model saved successfully")

            # whether to save checkpoint or not
            if train_args.check_point_mode in ["save", "load"]:
                torch.save({
                    "epoch": epoch,
                    "model_state_dict": train_args.model.state_dict(),
                    "optimizer_state_dict": optimizer.state_dict(),
                    "scheduler_state_dict" : scheduler.state_dict()
                }, train_args.check_point_path)
                logger.info(f"epoch {epoch} checkpoint saved successfully")
        logger.info("")


if __name__ == "__main__":
    Args = TrainArgs()
    torch.manual_seed(Args.args.random_seed)
    logger = get_logger(os.path.join(Args.args.exp_train_path, "log.txt"))
    if Args.args.check_point_mode != "load":
        save_script(Args.args.exp_train_path)
        with open(os.path.join(Args.args.exp_train_path, "config.yml"), "a") as f:
            yaml.dump({"args": Args.origin}, f, Dumper=yaml.RoundTripDumper)
            f.write("\n")

    trainer(Args.args, logger)



