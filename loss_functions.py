import torch.nn as nn

def image_loss_l1(model_out, gt, mask=None):
    gt_rgb = gt['rgb']
    return nn.L1Loss()(gt_rgb, model_out['rgb']) * 100

def image_loss_l2(model_out, gt, mask=None):
    gt_rgb = gt['rgb']
    return nn.MSELoss()(gt_rgb, model_out['rgb']) * 200


class LFLoss():
    def __init__(self, l2_weight=1, reg_weight=1e2):
        self.l2_weight = l2_weight
        self.reg_weight = reg_weight

    def __call__(self, model_out, gt, model=None, val=False):
        loss_dict = {}
        loss_dict['img_loss'] = image_loss_l2(model_out, gt)
        if 'z' in model_out:
            loss_dict['reg'] = (model_out['z']**2).mean() * self.reg_weight
        return loss_dict, {}


