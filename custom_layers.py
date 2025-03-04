import numpy as np
import geometry
from torchmeta.modules import (MetaModule, MetaSequential)
from collections import OrderedDict

import torch
from torch import nn
import tinycudann as tcnn


def init_weights_normal(m):
    if type(m) == BatchLinear or type(m) == nn.Linear:
        if hasattr(m, 'weight'):
            nn.init.kaiming_normal_(m.weight, a=0.0, nonlinearity='relu', mode='fan_in')


def first_layer_sine_init(m):
    with torch.no_grad():
        if hasattr(m, 'weight'):
            num_input = m.weight.size(-1)
            # See paper sec. 3.2, final paragraph, and supplement Sec. 1.5 for discussion of factor 30
            m.weight.uniform_(-1 / num_input, 1 / num_input)

def get_encoding_config(input_encoding, n_dims_to_encode=-1):
    config = {} if n_dims_to_encode <= 0 else { "n_dims_to_encode": n_dims_to_encode }
    if input_encoding == 'identity':
        config["otype"] = "Identity"
    elif input_encoding == 'positional':
        config["otype"] = "Frequency"
        config["n_frequencies"] = 8
    elif input_encoding == 'grid':
        config["otype"] = "HashGrid"
        config["n_levels"] = 16
        config["n_features_per_level"] = 2
        config["log2_hashmap_size"] = 19
        config["base_resolution"] = 16
        config["per_level_scale"] = 2.0

    return config

class BatchLinear(nn.Linear, MetaModule):
    '''A linear meta-layer that can deal with batched weight matrices and biases, as for instance output by a
    hypernetwork.'''
    __doc__ = nn.Linear.__doc__

    def forward(self, input, params=None):
        if params is None:
            params = OrderedDict(self.named_parameters())

        bias = params.get('bias', None)
        weight = params['weight']

        output = input.matmul(weight.permute(*[i for i in range(len(weight.shape) - 2)], -1, -2))
        output += bias.unsqueeze(-2)
        return output


class FCLayer(MetaModule):
    def __init__(self, in_features, out_features, nonlinearity='relu', norm=None):
        super().__init__()
        self.net = [BatchLinear(in_features, out_features)]

        if norm == 'layernorm':
            self.net.append(nn.LayerNorm([out_features], elementwise_affine=True),)
        elif norm == 'layernorm_na':
            self.net.append(nn.LayerNorm([out_features], elementwise_affine=False),)

        if nonlinearity == 'relu':
            self.net.append(nn.ReLU(inplace=True))
        elif nonlinearity == 'leaky_relu':
            self.net.append(nn.LeakyReLU(0.2, inplace=True))
        self.net = MetaSequential(*self.net)
        self.net.apply(init_weights_normal)

    def forward(self, input, params=None):
        return self.net(input, params=self.get_subdict(params, 'net'))


class FCBlock(MetaModule):
    def __init__(self,
                 hidden_ch,
                 num_hidden_layers,
                 in_features,
                 out_features,
                 outermost_linear=False,
                 norm=None,
                 activation='relu',
                 nonlinearity='relu'):
        super().__init__()

        self.net = []
        self.net.append(FCLayer(in_features=in_features, out_features=hidden_ch, nonlinearity=nonlinearity, norm=norm))

        for i in range(num_hidden_layers):
            self.net.append(FCLayer(in_features=hidden_ch, out_features=hidden_ch, nonlinearity=nonlinearity, norm=norm))

        if outermost_linear:
            self.net.append(BatchLinear(in_features=hidden_ch, out_features=out_features))
        else:
            self.net.append(FCLayer(in_features=hidden_ch, out_features=out_features, nonlinearity=nonlinearity, norm=norm))

        self.net = MetaSequential(*self.net)
        self.net.apply(init_weights_normal)

    def forward(self, input, params=None):
        return self.net(input, params=self.get_subdict(params, 'net'))


class SineLayer(MetaModule):
    def __init__(self, in_features, out_features, bias=True, is_first=False, omega_0=30):
        super().__init__()
        self.omega_0 = float(omega_0)

        self.is_first = is_first

        self.in_features = in_features
        self.linear = BatchLinear(in_features, out_features, bias=bias)
        self.init_weights()

    def init_weights(self):
        with torch.no_grad():
            if self.is_first:
                self.linear.weight.uniform_(-1 / self.in_features,
                                            1 / self.in_features)
            else:
                self.linear.weight.uniform_(-np.sqrt(6 / self.in_features) / self.omega_0,
                                            np.sqrt(6 / self.in_features) / self.omega_0)

    def forward_with_film(self, input, gamma, beta):
        intermed = self.linear(input)
        return torch.sin(gamma * self.omega_0 * intermed + beta)

    def forward(self, input, params=None):
        intermed = self.linear(input, params=self.get_subdict(params, 'linear'))
        return torch.sin(self.omega_0 * intermed)


class Siren(MetaModule):
    def __init__(self, in_features, hidden_features, hidden_layers, out_features, outermost_linear=False,
                 first_omega_0=30, hidden_omega_0=30., special_first=True):
        super().__init__()
        self.hidden_omega_0 = hidden_omega_0

        layer = SineLayer

        self.net = []
        self.net.append(layer(in_features, hidden_features,
                              is_first=special_first, omega_0=first_omega_0))

        for i in range(hidden_layers):
            self.net.append(layer(hidden_features, hidden_features,
                                  is_first=False, omega_0=hidden_omega_0))

        if outermost_linear:
            final_linear = BatchLinear(hidden_features, out_features)

            with torch.no_grad():
                final_linear.weight.uniform_(-np.sqrt(6 / hidden_features) / 30.,
                                             np.sqrt(6 / hidden_features) / 30.)
            self.net.append(final_linear)
        else:
            self.net.append(layer(hidden_features, out_features, is_first=False, omega_0=hidden_omega_0))

        self.net = nn.ModuleList(self.net)

    def forward(self, coords, params=None):
        x = coords

        for i, layer in enumerate(self.net):
            x = layer(x, params=self.get_subdict(params, f'net.{i}'))

        return x

    def forward_with_film(self, coords, film):
        x = coords

        for i, (layer, layer_film) in enumerate(zip(self.net, film)):
            if i < len(self.net) - 1:
                x = layer.forward_with_film(x, layer_film['gamma'], layer_film['beta'])
            else:
                x = layer.forward(x)

        return x

class PositionalEncoding(nn.Module):
    def __init__(self, in_features, n_dims):
        super().__init__()
        self.in_features = in_features
        self.n_dims = n_dims

    def out_size(self):
        return self.in_features * self.n_dims * 2
    
    def forward(self, x):
        y = []
        for i in range(self.n_dims):
            y.append(torch.sin(2**i * np.pi * x))
            y.append(torch.cos(2**i * np.pi * x))
        
        return torch.cat(y, dim=-1)

class FullyFusedFC(nn.Module):
    def __init__(self, in_features, out_features, num_hidden_layers, hidden_size, input_encoding='identity', repeat_nested_encoding=False):
        super().__init__()
        if input_encoding == 'grid' and in_features > 3:
            encoding_config = {
                "otype": "Composite",
                "nested": []
            }
            if repeat_nested_encoding:
                dims_remaining = in_features
                while dims_remaining > 0:
                    n_dims_to_encode = min(dims_remaining, 3)
                    encoding_config["nested"].append(get_encoding_config(input_encoding=input_encoding, n_dims_to_encode=n_dims_to_encode))
                    dims_remaining = dims_remaining - n_dims_to_encode
            else:
                encoding_config["nested"].append(get_encoding_config(input_encoding=input_encoding, n_dims_to_encode=2))
                encoding_config["nested"].append(get_encoding_config('identity', in_features-3))
        else:
            encoding_config = get_encoding_config(input_encoding=input_encoding)

        model_config = {
            "otype": "FullyFusedMLP",
            "activation": "ReLU",
            "output_activation": "None",
            "n_neurons": hidden_size,
            "n_hidden_layers": num_hidden_layers
        }
        self.net = tcnn.NetworkWithInputEncoding(
            in_features, out_features,
            encoding_config, model_config
        )

    def forward(self, x):
        shape = x.shape
        return self.net(x.reshape(-1, shape[-1])).reshape(shape[0], shape[1], -1).float()