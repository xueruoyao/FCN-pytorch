"""
It is recommended that data augmentation is finished before training, which means
there wouldn't be any dynamic preprocessing in training process except normalisation.

We provide simple data augmentation code in utils/data_augmentation.py
"""

import datetime
import logging
import os.path
import random
import cv2
import numpy as np
from tqdm import tqdm
import torch
from torch import optim, nn
from args import TrainArgs
import argparse
from utils import ClassificationEvaluator, SegmentationEvaluator
from utils import PNGTrainloader, TIFFTrainloader


def trainer(train_args: argparse):
    logging.info(datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]"))
    # 1. -------------Prepare dataloader, optimizer, scheduler, loss, evaluator---------------------------
    train_args.model.to(train_args.device)
    train_dataloader = PNGTrainloader(image_path=os.path.join(train_args.train_data_path, "image"),
                                      gt_path=os.path.join(train_args.train_data_path, "gt"),
                                      batch_size=train_args.batch_size, drop_last=True, shuffle=True)
    criterion = nn.CrossEntropyLoss()
    evaluator = SegmentationEvaluator(true_label=range(12))
    optimizer = optim.SGD(train_args.model.parameters(), lr=train_args.lr, momentum=0.9)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=2,
                                                     min_lr=1e-6, verbose=True)

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
        loss_ = torch.tensor([0], dtype=torch.float32, device=train_args.device)
        with tqdm(total=int(len(train_dataloader) / train_args.batch_size),
                  unit_scale=True, unit=" img", colour="cyan", ncols=60) as pbar:
            for i, (images, gts) in enumerate(train_dataloader):
                images = images.to(train_args.device)
                gts = gts.to(train_args.device)
                optimizer.zero_grad()
                predictions = train_args.model(images)
                loss = criterion(predictions, gts)
                loss_ += loss
                evaluator.accumulate(torch.argmax(predictions, dim=1), gts)
                loss.backward()
                optimizer.step()
                batch_num = i
                pbar.update(1)

        logging.info(f"epoch: {epoch}    loss: {round(loss_.item() / (batch_num + 1), 5)}")
        evaluator.log_metrics()
        evaluator.clear()
        scheduler.step(loss_)
        if epoch % 5 == 0:
            torch.save(train_args.model.state_dict(),
                       os.path.join(train_args.save_model_path, "model_epoch_{}.pth".format(epoch)))
            logging.info(f"\nepoch {epoch} model saved successfully\n")

            # whether to save checkpoint or not
            if train_args.check_point_mode in ["save", "load"]:
                torch.save({
                    "epoch": epoch,
                    "model_state_dict": train_args.model.state_dict(),
                    "optimizer_state_dict": optimizer.state_dict(),
                    "scheduler_state_dict" : scheduler.state_dict()
                }, train_args.check_point_path)
                logging.info(f"epoch {epoch} checkpoint saved successfully\n")


if __name__ == "__main__":
    torch.manual_seed(1)
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    args = TrainArgs()
    trainer(args.args)



