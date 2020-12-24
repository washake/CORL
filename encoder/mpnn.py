import torch
import torch.nn as nn
import torch.nn.functional as F


class MPNN(nn.Module):
    def __init__(
        self,
        embed_dim,
        problem,
        n_obs_in=1,
        n_layers=4,
        tied_weights=False,
        n_hid_readout=[],
        n_heads=None,
        node_dim=None,
        normalization=None,
        feed_forward_hidden=None,
    ):

        super().__init__()
        n_features = embed_dim
        self.n_obs_in = n_obs_in
        self.n_layers = n_layers
        self.n_features = n_features
        self.tied_weights = tied_weights
        bias_act = False
        self.node_init_embedding_layer = nn.Sequential(
            nn.Linear(n_obs_in, n_features, bias=True), nn.ReLU()
        )

        self.edge_embedding_layer = EdgeAndNodeEmbeddingLayer(n_obs_in, n_features, bias_act)

        if self.tied_weights:
            self.update_node_embedding_layer = UpdateNodeEmbeddingLayer(n_features, bias_act)
        else:
            self.update_node_embedding_layer = nn.ModuleList(
                [UpdateNodeEmbeddingLayer(n_features, bias_act) for _ in range(self.n_layers)]
            )

        # self.readout_layer = ReadoutLayer(n_features, n_hid_readout)

    @torch.no_grad()
    def get_normalisation(self, adj):
        norm = torch.sum((adj != 0), dim=1).unsqueeze(-1)
        norm[norm == 0] = 1
        return norm.float()

    def forward(self, node_features, adj, weights):
        # if obs.dim() == 2:
        #     obs = obs.unsqueeze(0)
        batch_size = adj.shape[0]
        graph_size = node_features.size(1)
        v = graph_size - weights.size(2)
        u = weights.size(2)
        weights1 = torch.cat(
            (
                torch.zeros((batch_size, u, u), device=weights.device),
                weights[:, :v, :].transpose(1, 2).float(),
            ),
            dim=2,
        )
        weights2 = torch.cat(
            (
                weights[:, :v, :].float(),
                torch.zeros((batch_size, v, v), device=weights.device),
            ),
            dim=2,
        )
        weights = torch.cat((weights1, weights2), dim=1)
        # print(weights)
        # print(adj)
        norm = self.get_normalisation(1. - adj)
        #print((1. - adj) == (weights != 0).float())
        adj = 1. - adj
        # obs.transpose_(-1, -2)

        # Calculate features to be used in the MPNN
        node_features = node_features
        # Get graph adj matrix.
        # adj = adj
        # adj_conns = (adj != 0).type(torch.FloatTensor).to(adj.device)

        # norm = self.get_normalisation(adj)

        init_node_embeddings = self.node_init_embedding_layer(node_features)
        edge_embeddings = self.edge_embedding_layer(node_features, adj, weights, norm)

        # Initialise embeddings.
        current_node_embeddings = init_node_embeddings

        if self.tied_weights:
            for i in range(self.n_layers):
                last_layer = i == self.n_layers - 1
                current_node_embeddings = self.update_node_embedding_layer(
                    current_node_embeddings,
                    edge_embeddings,
                    norm,
                    adj,
                    weights,
                    last_layer=last_layer,
                )
        else:
            for i in range(self.n_layers):
                last_layer = i == self.n_layers - 1
                current_node_embeddings = self.update_node_embedding_layer[i](
                    current_node_embeddings,
                    edge_embeddings,
                    norm,
                    adj,
                    weights,
                    last_layer=last_layer,
                )

        # out = self.readout_layer(current_node_embeddings)
        # out = out.squeeze()
        return current_node_embeddings


class EdgeAndNodeEmbeddingLayer(nn.Module):
    def __init__(self, n_obs_in, n_features, bias_act=True):
        super().__init__()
        self.n_obs_in = n_obs_in
        self.n_features = n_features

        self.edge_embedding_NN = nn.Linear(
            int(n_obs_in + 1), n_features - 1, bias=bias_act
        )
        self.edge_feature_NN = nn.Linear(n_features, n_features, bias=bias_act)

    def forward(self, node_features, adj, weights, norm):
        edge_features = torch.cat(
            [
                weights.unsqueeze(-1),
                node_features.unsqueeze(-2)
                .transpose(-2, -3)
                .repeat(1, adj.shape[-2], 1, 1),
            ],
            dim=-1,
        )
        edge_features *= (adj.unsqueeze(-1) != 0).float()

        edge_features_unrolled = torch.reshape(
            edge_features,
            (
                edge_features.shape[0],
                edge_features.shape[1] * edge_features.shape[1],
                edge_features.shape[-1],
            ),
        )
        embedded_edges_unrolled = F.relu(self.edge_embedding_NN(edge_features_unrolled))
        embedded_edges_rolled = torch.reshape(
            embedded_edges_unrolled,
            (adj.shape[0], adj.shape[1], adj.shape[1], self.n_features - 1),
        )
        embedded_edges = embedded_edges_rolled.sum(dim=2) / norm

        edge_embeddings = F.relu(
            self.edge_feature_NN(torch.cat([embedded_edges, norm / norm.max()], dim=-1))
        )

        return edge_embeddings


class UpdateNodeEmbeddingLayer(nn.Module):
    def __init__(self, n_features, bias_act=True):
        super().__init__()

        self.message_layer = nn.Linear(2 * n_features, n_features, bias=bias_act)
        self.update_layer = nn.Linear(2 * n_features, n_features, bias=bias_act)

    def forward(
        self,
        current_node_embeddings,
        edge_embeddings,
        norm,
        adj,
        weights,
        last_layer=False,
    ):
        node_embeddings_aggregated = (
            torch.matmul(weights, current_node_embeddings) / norm
        )

        message = F.relu(
            self.message_layer(
                torch.cat([node_embeddings_aggregated, edge_embeddings], dim=-1)
            )
        )
        if not last_layer:
            new_node_embeddings = F.relu(
                self.update_layer(torch.cat([current_node_embeddings, message], dim=-1))
            )
        else:
            new_node_embeddings = self.update_layer(
                torch.cat([current_node_embeddings, message], dim=-1)
            )

        return new_node_embeddings


class ReadoutLayer(nn.Module):
    def __init__(self, n_features, n_hid=[], bias_pool=False, bias_readout=True):

        super().__init__()

        self.layer_pooled = nn.Linear(int(n_features), int(n_features), bias=bias_pool)

        if type(n_hid) != list:
            n_hid = [n_hid]

        n_hid = [2 * n_features] + n_hid + [1]

        self.layers_readout = []
        for n_in, n_out in list(zip(n_hid, n_hid[1:])):
            layer = nn.Linear(n_in, n_out, bias=bias_readout)
            self.layers_readout.append(layer)

        self.layers_readout = nn.ModuleList(self.layers_readout)

    def forward(self, node_embeddings):

        f_local = node_embeddings

        h_pooled = self.layer_pooled(
            node_embeddings.sum(dim=1) / node_embeddings.shape[1]
        )
        f_pooled = h_pooled.repeat(1, 1, node_embeddings.shape[1]).view(
            node_embeddings.shape
        )

        features = F.relu(torch.cat([f_pooled, f_local], dim=-1))

        for i, layer in enumerate(self.layers_readout):
            features = layer(features)
            if i < len(self.layers_readout) - 1:
                features = F.relu(features)
            else:
                out = features

        return out