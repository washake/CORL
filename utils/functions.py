import torch
import numpy as np
import os
from tqdm import tqdm
from multiprocessing.dummy import Pool as ThreadPool
from multiprocessing import Pool
from problem_state.obm_dataset import Bipartite
from problem_state.edge_obm_dataset import EdgeBipartite
from problem_state.osbm_dataset import OSBM
from problem_state.adwords_dataset import AdwordsBipartite
import torch.nn.functional as F
import csv


def load_problem(name):

    problem = {
        "obm": Bipartite,
        "e-obm": EdgeBipartite,
        "osbm": OSBM,
        "adwords": AdwordsBipartite,
    }.get(name, None)
    assert problem is not None, "Currently unsupported problem: {}!".format(name)
    return problem


def torch_load_cpu(load_path):
    return torch.load(
        load_path, map_location=lambda storage, loc: storage
    )  # Load on CPU


def move_to(var, device):
    if isinstance(var, dict):
        return {k: move_to(v, device) for k, v in var.items()}
    elif isinstance(var, list):
        return list(move_to(v, device) for v in var)
    return var.to(device)


def _load_model_file(load_path, model):
    """Loads the model with parameters from the file and returns optimizer state dict if it is in the file"""

    # Load the model parameters from a saved state
    load_optimizer_state_dict = None
    print("  [*] Loading model from {}".format(load_path))

    load_data = torch.load(
        os.path.join(os.getcwd(), load_path), map_location=lambda storage, loc: storage
    )

    if isinstance(load_data, dict):
        load_optimizer_state_dict = load_data.get("optimizer", None)
        load_model_state_dict = load_data.get("model", load_data)
    else:
        load_model_state_dict = load_data.state_dict()

    state_dict = model.state_dict()

    state_dict.update(load_model_state_dict)

    model.load_state_dict(state_dict)

    return model, load_optimizer_state_dict


def get_best_t(model, opts):
    best_params = None
    best_r = 0
    with open(
        f"val_rewards_{model}_{opts.u_size}_{opts.v_size}_{opts.graph_family}_{opts.graph_family_parameter}.csv"
    ) as csv_file:
        csv_reader = csv.reader(csv_file, delimiter=",")
        for line in csv_reader:
            if abs(float(line[-1])) > best_r:
                best_params = float(line[0])
                best_r = abs(float(line[-1]))
    return best_params


def parse_softmax_temperature(raw_temp):
    # Load from file
    if os.path.isfile(raw_temp):
        return np.loadtxt(raw_temp)[-1, 0]
    return float(raw_temp)


def run_all_in_pool(func, directory, dataset, opts, use_multiprocessing=True):
    # # Test
    # res = func((directory, 'test', *dataset[0]))
    # return [res]

    num_cpus = os.cpu_count() if opts.cpus is None else opts.cpus

    w = len(str(len(dataset) - 1))
    offset = getattr(opts, "offset", None)
    if offset is None:
        offset = 0
    ds = dataset[offset : (offset + opts.n if opts.n is not None else len(dataset))]
    pool_cls = Pool if use_multiprocessing and num_cpus > 1 else ThreadPool
    with pool_cls(num_cpus) as pool:
        results = list(
            tqdm(
                pool.imap(
                    func,
                    [
                        (directory, str(i + offset).zfill(w), *problem)
                        for i, problem in enumerate(ds)
                    ],
                ),
                total=len(ds),
                mininterval=opts.progress_bar_mininterval,
            )
        )

    failed = [str(i + offset) for i, res in enumerate(results) if res is None]
    assert len(failed) == 0, "Some instances failed: {}".format(" ".join(failed))
    return results, num_cpus


def do_batch_rep(v, n):
    if isinstance(v, dict):
        return {k: do_batch_rep(v_, n) for k, v_ in v.items()}
    elif isinstance(v, list):
        return [do_batch_rep(v_, n) for v_ in v]
    elif isinstance(v, tuple):
        return tuple(do_batch_rep(v_, n) for v_ in v)

    return v[None, ...].expand(n, *v.size()).contiguous().view(-1, *v.size()[1:])


def sample_many(inner_func, get_cost_func, input, batch_rep=1, iter_rep=1):
    """
    :param input: (batch_size, graph_size, node_dim) input node features
    :return:
    """
    input = do_batch_rep(input, batch_rep)

    costs = []
    pis = []
    for i in range(iter_rep):
        _log_p, pi = inner_func(input)
        # pi.view(-1, batch_rep, pi.size(-1))
        cost, mask = get_cost_func(input, pi)

        costs.append(cost.view(batch_rep, -1).t())
        pis.append(pi.view(batch_rep, -1, pi.size(-1)).transpose(0, 1))

    max_length = max(pi.size(-1) for pi in pis)
    # (batch_size * batch_rep, iter_rep, max_length) => (batch_size, batch_rep * iter_rep, max_length)
    pis = torch.cat(
        [F.pad(pi, (0, max_length - pi.size(-1))) for pi in pis], 1
    )  # .view(embeddings.size(0), batch_rep * iter_rep, max_length)
    costs = torch.cat(costs, 1)

    # (batch_size)
    mincosts, argmincosts = costs.min(-1)
    # (batch_size, minlength)
    minpis = pis[torch.arange(pis.size(0), out=argmincosts.new()), argmincosts]

    return minpis, mincosts
