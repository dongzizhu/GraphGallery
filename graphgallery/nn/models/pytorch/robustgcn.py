import torch
import torch.nn as nn
from torch import optim

from graphgallery.nn.models import TorchEngine
from graphgallery.nn.layers.pytorch import GaussionConvF, GaussionConvD, activations
from graphgallery.nn.metrics import Accuracy


class RobustGCN(TorchEngine):
    def __init__(self,
                 in_features,
                 out_features,
                 *,
                 hids=[64],
                 acts=['relu'],
                 dropout=0.5,
                 weight_decay=5e-4,
                 lr=0.01, kl=5e-4, gamma=1.,
                 bias=False):

        super().__init__()

        assert len(hids) > 0 and len(acts) > 0
        # The first layer that conver node features to distribution
        self.conv1 = GaussionConvF(in_features,
                                   hids[0], gamma=gamma,
                                   bias=bias)
        self.act1 = activations.get(acts[0])
        in_features = hids[0]

        conv2 = nn.ModuleList()
        act2 = nn.ModuleList()
        for hid, act in zip(hids[1:], acts[1:]):
            conv2.append(GaussionConvD(in_features,
                                       hid, gamma=gamma,
                                       bias=bias))
            act2.append(activations.get(act))
            in_features = hid

        conv2.append(GaussionConvD(in_features, out_features, bias=bias))
        self.conv2 = conv2
        self.act2 = act2
        self.kl = kl
        self.dropout = nn.Dropout(dropout)
        self.compile(loss=nn.CrossEntropyLoss(),
                     optimizer=optim.Adam(self.parameters(),
                                          weight_decay=weight_decay, lr=lr),
                     metrics=[Accuracy()])

    def forward(self, x, adj_mean, adj_var):
        mean, var = self.conv1(x, adj_mean, adj_var)
        mean, var = self.act1(mean), self.act1(var)
        output_dict = dict(mean=mean, var=var)

        for conv, act in zip(self.act2, self.conv2[:-1]):
            mean, var = conv(mean, var, adj_mean, adj_var)
            mean, var = act(mean), act(var)

        mean, var = self.conv2[-1](mean, var, adj_mean, adj_var)

        std = torch.sqrt(var + 1e-8)
        eps = torch.randn_like(std)
        z = eps.mul(std).add_(mean)
        output_dict['z'] = z

        return output_dict

    def compute_loss(self, output_dict, y):
        loss = self.loss(output_dict['pred'], y)
        mu = output_dict['mean']
        var = output_dict['var']
        kl_loss = -0.5 * torch.sum(torch.mean(1 + torch.log(var + 1e-8) - mu.pow(2) + var, dim=1))
        return loss + self.kl * kl_loss
