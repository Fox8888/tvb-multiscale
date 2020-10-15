# -*- coding: utf-8 -*-

from collections import OrderedDict
from copy import deepcopy

import numpy as np

from tvb_multiscale.tvb_nest.config import CONFIGURED
from tvb_multiscale.tvb_nest.nest_models.builders.base import NESTModelBuilder
from tvb_multiscale.core.spiking_models.builders.templates import tvb_delay, scale_tvb_weight


class TVBWeightFun(object):
    tvb_weights = np.array([])
    global_coupling_scaling = 1.0
    sign = 1

    def __init__(self, tvb_weights, global_coupling_scaling=1.0, sign=1):
        self.tvb_weights = tvb_weights
        self.global_coupling_scaling = global_coupling_scaling
        self.sign = sign

    def __call__(self, source_node, target_node):
        return scale_tvb_weight(source_node, target_node, self.tvb_weights,
                                scale=self.sign*self.global_coupling_scaling)


class BasalGangliaIzhikevichBuilder(NESTModelBuilder):

    def __init__(self, nest_nodes_ids, nest_instance=None, config=CONFIGURED, **tvb_params):
        # NOTE!!! TAKE CARE OF DEFAULT simulator.coupling.a!
        self.global_coupling_scaling = tvb_params.pop("coupling_a", 1.0 / 256.0)
        # if we use Reduced Wong Wang model, we also need to multiply with the global coupling constant G:
        self.global_coupling_scaling *= tvb_params.pop("G", 20.0)
        super(BasalGangliaIzhikevichBuilder, self).__init__(nest_nodes_ids, nest_instance, config, **tvb_params)
        self.default_population["model"] = "izhikevich_hamker"

        # Common order of neurons' number per population:
        self.population_order = 200

        self.params_common = {"E_rev_AMPA": 0.0, "E_rev_GABA_A": -90.0, "V_th": 30.0, "c": -65.0,
                              "C_m": 1.0, "I_e": 0.0,
                              "t_ref": 10.0, "tau_rise": 1.0, "tau_rise_AMPA": 10.0, "tau_rise_GABA_A": 10.0,
                              "n0": 140.0, "n1": 5.0, "n2": 0.04}
        self._paramsI = deepcopy(self.params_common)
        self._paramsI.update({"a": 0.005, "b": 0.585, "d": 4.0})
        self._paramsE = deepcopy(self.params_common)
        self.paramsStr = deepcopy(self.params_common)
        self.paramsStr.update({"V_th": 40.0, "C_m": 50.0,
                               "n0": 61.65, "n1": 2.59, "n2": 0.02,
                               "a": 0.05, "b": -20.0, "c": -55.0, "d": 377.0})

        self.Igpe_nodes_ids = [0, 1]
        self.Igpi_nodes_ids = [2, 3]
        self.Estn_nodes_ids = [4, 5]
        self.Eth_nodes_ids = [8, 9]
        self.Istr_nodes_ids = [6, 7]

        self.Estn_stim = {"rate": 500.0, "weight": 0.009}
        self.Igpe_stim = {"rate": 100.0, "weight": 0.015}
        self.Igpi_stim = {"rate": 700.0, "weight": 0.02}

        self.populations = [
            {"label": "E", "model": self.default_population["model"],  # Estn in [4, 5], Eth in [8, 9]
             "params": self.paramsE, "nodes": self.Estn_nodes_ids + self.Eth_nodes_ids,  # None means "all"
             "scale": 1.0},
            {"label": "I", "model": self.default_population["model"],  # Igpe in [0, 1], Igpi in [2, 3]
             "params": self.paramsI, "nodes": self.Igpe_nodes_ids + self.Igpi_nodes_ids,  # None means "all"
             "scale": 1.0},
            {"label": "I1", "model": self.default_population["model"],  # Isd1 in [6, 7]
             "params": self.paramsStr, "nodes": self.Istr_nodes_ids,  # None means "all"
             "scale": 1.0},
            {"label": "I2", "model": self.default_population["model"],  # Isd2 in [6, 7]
             "params": self.paramsStr, "nodes": self.Istr_nodes_ids,  # None means "all"
             "scale": 1.0}
        ]

        synapse_model = self.default_populations_connection["synapse_model"]  # "static_synapse"
        # default connectivity spec:
        # conn_spec = {"autapses": True, 'multapses': True, 'rule': "all_to_all",
        #              "indegree": None, "outdegree": None, "N": None, "p": 0.1}
        conn_spec = self.default_populations_connection["conn_spec"]

        # Intra-regions'-nodes' connections
        self.populations_connections = []
        for pop in self.populations:
            # Only self-connections and only for all inhibitory  populations
            if pop["label"][0] == "I":
                self.populations_connections.append(
                    {"source": pop["label"], "target": pop["label"],
                     "synapse_model": synapse_model, "conn_spec": conn_spec,
                     "weight": -1.0, "delay": self.default_min_delay,  # 0.001
                     "receptor_type": 0, "nodes": pop["nodes"]})

        # Inter-regions'-nodes' connections
        self.nodes_connections = []
        for src_pop, trg_pop, src_nodes, trg_nodes in \
            zip(
               # "Isd1->Igpi", "Isd2->Igpe", "Igpe->Igpi", "Igpi->Eth", "Igpe->Estn", "Eth->[Isd1, Isd2]", "Estn->[Igpe, Igpi]",
                ["I1",         "I2",         "I",          "I",         "I",          "E",                 "E"],  # source
                ["I",          "I",          "I",          "E",         "E",          ["I1", "I2"],        "I"],  # target
                [[6, 7],       [6, 7],       [0, 1],       [2, 3],      [0, 1],       [8, 9],              [4, 5]],  # source nodes
                [[2, 3],       [0, 1],       [2, 3],       [8, 9],      [4, 5],       [6, 7],              [0, 1, 2, 3]]):  # target nodes
            if src_pop[0] == "I":
                sign = -1
            else:
                sign = 1
            self.nodes_connections.append(
                    {"source": src_pop, "target": trg_pop,
                     "synapse_model": self.default_nodes_connection["synapse_model"],
                     "conn_spec": self.default_nodes_connection["conn_spec"],
                     "weight": TVBWeightFun(self.tvb_weights, self.global_coupling_scaling, sign),
                     "delay": lambda source_node, target_node: self.tvb_delay_fun(source_node, target_node),
                     "receptor_type": 0, "source_nodes": src_nodes, "target_nodes": trg_nodes})

        # Creating  devices to be able to observe NEST activity:
        self.output_devices = []
        #          label <- target population
        for pop in self.populations:
            connections = OrderedDict({})
            connections[pop["label"] + "_spikes"] = pop["label"]
            self.output_devices.append(
                {"model": "spike_recorder", "params": {},
                 "connections": connections, "nodes": pop["nodes"]})  # None means apply to "all"

        # Labels have to be different for every connection to every distinct population
        params = {"interval": 1.0,
                  'record_from': ["V_m", "U_m", "I_syn", "I_syn_ex", "I_syn_in", "g_L", "g_AMPA", "g_GABA_A"]}
        for pop in self.populations:
            connections = OrderedDict({})
            #               label    <- target population
            connections[pop["label"]] = pop["label"]
            self.output_devices.append(
                {"model": "multimeter", "params": params,
                 "connections": connections, "nodes": pop["nodes"]})  # None means apply to all

        # Create a spike stimulus input device
        self.input_devices = [
            {"model": "poisson_generator",
             "params": {"rate": self.Estn_stim["rate"], "origin": 0.0, "start": 0.1},
             "connections": {"BaselineEstn": ["E"]},  # "Estn"
             "nodes": self.Estn_nodes_ids,  # None means apply to all
             "weights": self.Estn_stim["weight"], "delays": 0.0, "receptor_type": 1},
            {"model": "poisson_generator",
             "params": {"rate": self.Igpe_stim["rate"], "origin": 0.0, "start": 0.1},
             "connections": {"BaselineIgpe": ["I"]},  # "Igpe"
             "nodes": self.Igpe_nodes_ids,  # None means apply to all
             "weights": self.Igpe_stim["weight"], "delays": 0.0, "receptor_type": 1},
            {"model": "poisson_generator",
             "params": {"rate": self.Igpi_stim["rate"], "origin": 0.0, "start": 0.1},
             "connections": {"BaselineIgpi": ["I"]},  # "Igpi"
             "nodes": self.Igpi_nodes_ids,  # None means apply to all
             "weights": self.Igpi_stim["weight"], "delays": 0.0, "receptor_type": 1},
            # {"model": "ac_generator",
            #  "params": {"frequency": 30.0, "phase": 0.0, "amplitude": 1.0, "offset": 0.0,
            #             "start": 1.0},  # "stop": 100.0  "origin": 0.0,
            #  "connections": {"DBS_Estn": ["E"]},  # "Estn"
            #  "nodes": self.Estn_nodes_ids,  # None means apply to all
            #  "weights": 1.0, "delays": 0.0}
        ]  #

    def paramsI(self, node_id):
        # For the moment they are identical, unless you differentiate the noise parameters
        params = deepcopy(self._paramsI)
        if node_id in self.Igpe_nodes_ids:
            params.update({"I_e": 12.0})
        elif node_id in self.Igpi_nodes_ids:
            params.update({"I_e": 30.0})
        return params

    def paramsE(self, node_id):
        # For the moment they are identical, unless you differentiate the noise parameters
        params = deepcopy(self._paramsE)
        if node_id in self.Estn_nodes_ids:
            params.update({"a": 0.005, "b": 0.265, "d": 2.0, "I_e": 3.0})
        elif node_id in self.Eth_nodes_ids:
            params.update({"a": 0.02, "b": 0.25, "d": 0.05, "I_e": 3.5})
        return params

    def tvb_delay_fun(self, source_node, target_node):
        return np.maximum(self.tvb_dt, tvb_delay(source_node, target_node, self.tvb_delays))
