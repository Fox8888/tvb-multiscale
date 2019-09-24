# -*- coding: utf-8 -*-

from tvb_nest.simulator_nest.models.region_node import NESTRegionNode
from tvb_nest.simulator_nest.models.network import NESTNetwork
from tvb_nest.simulator_nest.nest_factory import *
from tvb_scripts.utils.log_error_utils import initialize_logger
from tvb_scripts.utils.data_structures_utils import ensure_list, flatten_tuple
from tvb_scripts.utils.indexed_ordered_dict import IndexedOrderedDict, OrderedDict

LOG = initialize_logger(__name__)


class NESTModelBuilder(object):
    config = CONFIGURED
    default_model = config.nest.DEFAULT_MODEL
    default_synapse = config.nest.DEFAULT_SYNAPSE
    default_synaptic_weight_scaling = \
        lambda self, weight, n_cons: self.config.nest.DEFAULT_NEST_SYNAPTIC_WEIGHT_SCALING(weight, n_cons)
    nest_instance = None
    nodes = []
    tvb_connectivity = None
    tvb_dt = 0.1
    tvb_to_nest_dt_ratio = 10
    nest_dt = tvb_dt / tvb_to_nest_dt_ratio
    tvb_weights = np.array([])
    tvb_delays = np.array([])
    nest_nodes_ids = []
    _nest_nodes_labels = []

    populations_order = 100
    populations_names = ["E"]  # e.g., "E" for excitatory population
    populations_models = [default_model]
    populations_scales = [1.0]
    populations_params = [{}]
    populations_sizes = []

    # Within NEST node delays should be at least equal to NEST time resolution,
    # and certainly much smaller than the TVB time resolution.
    population_connectivity_synapses_weights = default_synapse["weights"]
    population_connectivity_synapses_delays = default_synapse["delays"]
    population_connectivity_synapses_model = np.array([default_synapse["model"]])
    population_connectivity_synapses_params = np.array([default_synapse["params"]])

    # Between NEST node delays should be at least equal to NEST time resolution
    # Therefore, 0 TVB delays will become nest_dt delays in NEST
    node_connections = [{"src_population": "E", "trg_population": "E",
                         "model": default_synapse["model"],
                         "params": default_synapse["params"],
                         "weight": 1.0,  # weight scaling the TVB connectivity weight
                         "delay": 0.0}]  # additional delay to the one of TVB connectivity

    # Use these to observe NEST behavior without conversions to TVB state variables and monitors
    output_devices = [{"model": "spike_detector",
                       "props": config.nest.NEST_OUTPUT_DEVICES_PARAMS_DEF["spike_detector"],
                       "nodes": None, "connections": {"n_spikes_E": "E"}}]
    stimulation_devices = []  # use these for possible external stimulation devices

    # Example:
    # stimulation_devices = [{"model": "poisson_generator",
    #                         "props": {"rate": 50.0,
    #                                   "origin": 10.0,
    #                                   "start": 0.1,
    #                                   "stop": 20.0},
    #                        "nodes": None,  # 1 device per node
    #                        "populations: "E",
    #                        "weight": 1.0,
    #                        "delay": 0.0}]  # use these for possible external stimulation devices

    def __init__(self, tvb_simulator, nest_nodes_ids, nest_instance=None, config=CONFIGURED):
        self.config = config
        if nest_instance is not None:
            self.nest_instance = nest_instance
        else:
            self.nest_instance = load_nest()
        self.tvb_connectivity = tvb_simulator.connectivity
        self.nest_nodes_ids = nest_nodes_ids
        self.tvb_dt = tvb_simulator.integrator.dt

    @property
    def number_of_populations(self):
        return len(self.populations_names)

    @property
    def nest_nodes_labels(self):
        if len(self._nest_nodes_labels) == self.number_of_nest_nodes:
            return self._nest_nodes_labels
        else:
            return self.tvb_connectivity.region_labels[self.nest_nodes_ids]

    def assert_delay(self, delay):
        return np.maximum(self.nest_dt, delay)

    def assert_within_node_delay(self, delay):
        if delay > self.tvb_dt / 2:
            if delay > self.tvb_dt:
                raise ValueError("Within NEST node delay %f is not smaller "
                                 "than the TVB integration time step %f!"
                                 % (delay, self.tvb_dt))
            else:
                LOG.warning("Within NEST node delay %f is not smaller "
                            "than half the TVB integration time step %f!"
                            % (delay, self.tvb_dt))
        return self.assert_delay(delay)

    @property
    def number_of_nest_nodes(self):
        return np.maximum(len(self.nest_nodes_ids), 1)

    def _update_nest_dt(self):
        self.nest_dt = \
            float(int(np.round(self.tvb_dt / self.tvb_to_nest_dt_ratio / self.config.nest.NEST_MIN_DT))) \
            * self.config.nest.NEST_MIN_DT

    def _configure_nest_kernel(self):
        self.nest_instance.ResetKernel()  # This will restart NEST!
        self._update_nest_dt()
        self.nest_instance.set_verbosity(100)  # don't print all message from Nest
        self.nest_instance.SetKernelStatus({"resolution": self.nest_dt, "print_time": True})

    def _configure_populations(self):
        # Every population must have his own model name,
        # scale of spiking neurons' number, and model specific parameters
        for key in ['models', 'scales', 'params']:
            attr = 'populations_' + key
            setattr(self, attr, ensure_list(getattr(self, attr)))
            val = ensure_list(getattr(self, attr))
            n_temp = len(val)
            if n_temp != self.number_of_populations:
                if n_temp == 1:
                    setattr(self, attr, val * self.number_of_populations)
                else:
                    raise ValueError("populations_%s has neither length 1 "
                                     "nor  equal to the number %d of populations_names %s,\n"
                                     "but %d" % (attr, self.number_of_populations, self.populations_names, n_temp))
        self.populations_sizes = [int(np.round(scale * self.populations_order)) for scale in self.populations_scales]

    def _configure_populations_connectivity(self):
        # Inputs of synaptic model, weights, delays and synaptic parameters,
        # must have a shape that can propagate to the populations' connectivity shape
        conn_shape = (self.number_of_populations, self.number_of_populations)
        for key in ['model', 'delays', 'weights', 'params']:
            attr = 'population_connectivity_synapses_' + key
            val = np.array(ensure_list(getattr(self, attr)))
            if val.shape != conn_shape:
                if val.size == 1:
                    val = np.tile(val, conn_shape)
                else:
                    raise ValueError("%s shape %s does not propagate to the populations connectivty shape %s!"
                                     % (attr, str(val.shape), str(conn_shape)))
            setattr(self, attr, val)

    def _configure_nodes_connectivity(self):
        self.tvb_connectivity.configure()
        # Select the relevant delays and weights from TVB connectivity
        self.tvb_weights = self.tvb_connectivity.weights[self.nest_nodes_ids][:, self.nest_nodes_ids]
        # TODO: confirm units!
        self.tvb_delays = self.tvb_connectivity.delays[self.nest_nodes_ids][:, self.nest_nodes_ids]

    def configure(self):
        self._configure_populations()
        self._configure_populations_connectivity()
        self._configure_nodes_connectivity()

    def _synaptic_weight_scaling(self, weights, number_of_connections):
        return self.default_synaptic_weight_scaling(weights, number_of_connections)

    def _connect_two_populations(self, pop_src, pop_trg, conn_spec, syn_spec):
        conn_spec, n_cons = create_connection_dict(n_src=len(pop_src), n_trg=len(pop_trg),
                                                   src_is_trg=(pop_src == pop_trg), config=self.config, **conn_spec)
        # Scale the synaptic weight with respect to the total number of connections between the two populations:
        syn_spec["weight"] = self._synaptic_weight_scaling(syn_spec["weight"], n_cons)
        self.nest_instance.Connect(pop_src, pop_trg, conn_spec, syn_spec)

    def _connect_two_populations_within_node(self, pop_src, pop_trg, i_pop_src, i_pop_trg):
        conn_spec = self.default_synapse['params']
        conn_spec.update(self.population_connectivity_synapses_params[i_pop_src, i_pop_trg])
        syn_spec = {'model': self.population_connectivity_synapses_model[i_pop_src, i_pop_trg],
                    'weight': self.population_connectivity_synapses_weights[i_pop_src, i_pop_trg],
                    'delay': self.assert_within_node_delay(
                        self.population_connectivity_synapses_delays[i_pop_src, i_pop_trg])}
        self._connect_two_populations(pop_src, pop_trg, conn_spec, syn_spec)

    def connect_population(self, population, i_pop):
        self._connect_two_populations_within_node(population, population, i_pop, i_pop)

    def connect_nest_node_populations(self, node):
        # For every possible pair of populations with a node...
        for i_pop1 in range(self.number_of_populations - 1):
            for i_pop2 in range(i_pop1 + 1, self.number_of_populations):
                # ...generate the required connections (with weight > 0)
                if self.population_connectivity_synapses_weights[i_pop1, i_pop2]:
                    self._connect_two_populations_within_node(node[i_pop1], node[i_pop2], i_pop1, i_pop2)
                if self.population_connectivity_synapses_weights[i_pop2, i_pop1]:
                    self._connect_two_populations_within_node(node[i_pop2], node[i_pop1], i_pop2, i_pop1)

    def build_nest_populations(self, label):
        # Generate a NEST spiking network population...
        node = NESTRegionNode(self.nest_instance, OrderedDict({}), label)
        for iP, (name, model, sizes, params) in \
                enumerate(zip(self.populations_names,
                              self.populations_models,
                              self.populations_sizes,
                              self.populations_params)):
            node.update({name: self.nest_instance.Create(model, sizes, params=params)})
            # ...and connect it to itself:
            self.connect_population(node[name], iP)
        return node

    def build_nest_nodes(self):
        self.nodes = IndexedOrderedDict(OrderedDict({}))
        for node_label in self.nest_nodes_labels:  # For every NEST node
            # ...generate a network of spiking population
            self.nodes.update({node_label: self.build_nest_populations(node_label)})
            self.connect_nest_node_populations(self.nodes[node_label])  # ...and connect them

    def _connect_two_populations_between_nodes(self, pop_src, pop_trg, i_n_src, i_n_trg,
                                               conn_spec, syn_model, weight, delay):
        syn_spec = {'model': syn_model,
                    'weight': self.tvb_weights[i_n_src, i_n_trg] * weight,
                    'delay': self.assert_delay(self.tvb_delays[i_n_src, i_n_trg] + delay)}
        self._connect_two_populations(pop_src, pop_trg, conn_spec, syn_spec)

    def connect_nest_nodes(self):
        n_nodes = len(self.nest_nodes_ids)
        # For every different type of connections between distinct NEST nodes' populations
        for i_conn, conn in enumerate(ensure_list(self.node_connections)):
            conn_spec = self.default_synapse['params']
            conn_spec.update(conn['params'])
            model = conn.get("model", self.default_synapse["model"])
            weight = conn.get("weight", 1.0)
            delay = conn.get("delay", 0.0)
            # Define functions for the exact synthesis of source and target populations
            pop_src = lambda node: \
                flatten_tuple([node[pop]
                               for pop in ensure_list(conn['src_population'])])
            pop_trg = lambda node: \
                flatten_tuple([node[pop]
                               for pop in ensure_list(conn['trg_population'])])
            # ...and form the connection for every distinct pair of NEST nodes
            # TODO! Confirm that no self connections are allowed here!
            for i_n1 in range(n_nodes - 1):
                for i_n2 in range(1, n_nodes):
                    if self.tvb_weights[i_n1, i_n2] > 0:
                        self._connect_two_populations_between_nodes(pop_src(self.nodes[i_n1]),
                                                                    pop_trg(self.nodes[i_n2]),
                                                                    i_n1, i_n2, conn_spec,
                                                                    model, weight, delay)
                    if self.tvb_weights[i_n2, i_n1] > 0:
                        self._connect_two_populations_between_nodes(pop_src(self.nodes[i_n2]),
                                                                    pop_trg(self.nodes[i_n1]),
                                                                    i_n2, i_n1, conn_spec,
                                                                    model, weight, delay)

    def build_and_connect_nest_stimulation_devices(self):
        # Build devices by the variable name they stimulate (IndexedOrderedDict),
        # target node (IndexedOrderedDict)
        # and population (IndexedOrderedDict) for faster reading
        return build_and_connect_input_devices(self.nest_instance, self.stimulation_devices, self.nodes)

    def build_and_connect_nest_output_devices(self):
        # Build devices by the variable name they measure (IndexedOrderedDict),
        # target node (IndexedOrderedDict)
        # and population (IndexedOrderedDict) for faster reading
        return build_and_connect_output_devices(self.nest_instance, self.output_devices, self.nodes)

    def build_nest_network(self):
        self._configure_nest_kernel()
        # Configure all inputs to set them to the correct formats and sizes
        self.configure()
        # Build and connect internally all NEST nodes
        self.build_nest_nodes()
        # Connect NEST nodes among each other
        self.connect_nest_nodes()
        # Build and connect possible NEST output devices
        # !!Use it only for extra NEST quantities
        # that do not correspond to TVB state variables or parameters
        # you wish to transmit from NEST to TVB!!
        output_devices = self.build_and_connect_nest_output_devices()
        # Build and connect possible NEST input devices
        # !!Use it only for stimuli, if any!!
        stimulation_devices = self.build_and_connect_nest_stimulation_devices()
        return NESTNetwork(self.nest_instance, self.nodes, output_devices, stimulation_devices, self.config)