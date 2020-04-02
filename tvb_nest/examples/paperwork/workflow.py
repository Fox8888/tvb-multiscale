# -*- coding: utf-8 -*-
from six import string_types
import os
import time
from collections import OrderedDict
import numpy as np
from xarray import DataArray, concat

from tvb.basic.profile import TvbProfile
TvbProfile.set_profile(TvbProfile.LIBRARY_PROFILE)

from tvb_nest.config import CONFIGURED
from tvb_nest.nest_models.builders.models.ww_deco2014 import WWDeco2014Builder
from tvb_nest.interfaces.builders.models.ww_deco2014 import WWDeco2014Builder as InterfaceWWDeco2014Builder
from tvb_multiscale.plot.plotter import Plotter
from tvb.datatypes.connectivity import Connectivity
from tvb.simulator.simulator import Simulator
from tvb.simulator.integrators import HeunStochastic
from tvb.simulator.monitors import Raw
from tvb.simulator.models.reduced_wong_wang_exc_io_inh_i import ReducedWongWangExcIOInhI
from tvb_scripts.datatypes.time_series_xarray import TimeSeriesRegion
from tvb_scripts.io.h5_writer import H5Writer


CONFIGURED.NEST_MIN_DT = 0.01

CONFIGURED.DEFAULT_CONNECTION = {"model": "static_synapse", "weight": 1.0, "delay": 0.0, 'receptor_type': 0,
                                 "conn_spec": {"autapses": True, 'multapses': True, 'rule': "all_to_all",
                                               "indegree": None, "outdegree": None, "N": None, "p": 0.1}}

CONFIGURED.NEST_OUTPUT_DEVICES_PARAMS_DEF = \
    {"multimeter": {"withtime": True, "withgid": True, 'record_from': ["V_m"]},
     "voltimeter": {"withtime": True, "withgid": True},
     "spike_detector": {"withgid": True, "withtime": True, 'precise_times': True},
     "spike_multimeter": {"withtime": True, "withgid": True, 'record_from': ["spike"]}}

CONFIGURED.NEST_INPUT_DEVICES_PARAMS_DEF = \
    {"poisson_generator": {},
     "mip_generator": {"p_copy": 0.5, "mother_seed": 0},
     "inhomogeneous_poisson_generator": {"allow_offgrid_times": False}}


class Workflow(object):
    config = CONFIGURED

    writer = True
    plotter = True
    path = ""
    filename = ""
    h5_file = None

    connectivity_path = CONFIGURED.DEFAULT_CONNECTIVITY_ZIP
    decouple = False
    time_delays = False
    force_dims = None

    tvb_model = ReducedWongWangExcIOInhI

    dt = 0.1
    integrator = HeunStochastic
    #                              S_e,  S_i,  R_e, R_i
    tvb_noise_strength = np.array([0.01, 0.01, 0.0, 0.0])
    transient = 10.0
    simulation_length = 100.0

    nest_model_builder = WWDeco2014Builder
    nest_nodes_ids = []
    nest_populations_order = 100
    nest_stimulus_rate = 2400.0

    interface_builder = InterfaceWWDeco2014Builder
    interface = "rate"
    exclusive_nodes = True

    def __init__(self, **model_params):
        self.model_params = model_params

    @property
    def number_of_regions(self):
        return self.connectivity.number_of_regions

    def configure(self):
        if self.writer:
            if self.writer is True:
                self.writer = H5Writer()
            self.filename = "sim"
            for param, val in self.model_params.items():
                self.filename += ("_%s%.1f" % (param, val))
            self.path = os.path.join(self.config.out.FOLDER_RES, self.filename)
        if self.plotter:
            if self.plotter is True:
                self.plotter = Plotter(self.config)

    def write_group(self, data, name, method, close_file=True):
        if self.h5_file is None:
            self.h5_file = self.writer._open_file("multiple groups", self.path)
        getattr(self.writer, "write_%s" % method)(data, h5_file=self.h5_file[name])
        if close_file:
            self.h5_file.close()
        self.h5_file = None

    @property
    def general_parameters(self):
        return {"nest_nodes_ids": self.nest_nodes_ids, "interface": str(self.interface),
                 "tvb_connectivity_path": self.connectivity_path,
                "decouple": self.decouple, "time_delays": self.time_delays, "force_dims": str(self.force_dims),
                "transient": self.transient, "simulation_length": self.simulation_length,
                "filename": self.filename, "path": self.path
                }

    def write_general_params(self, close_file=True):
        self.write_group(self.general_params, "general_params", "dictionary", close_file=close_file)

    def write_model_params(self, close_file=True):
        self.write_group(self.model_params, "model_params", "dictionary", close_file=close_file)

    def force_dimensionality(self):
        dim = self.force_dims
        self.connectivity.weights = self.connectivity.weights[:dim][:, :dim]
        self.connectivity.tract_lengths = self.connectivity.tract_lengths[:dim][:, :dim]
        self.connectivity.centres = self.connectivity.centres[:dim]
        self.connectivity.areas = self.connectivity.areas[:dim]
        self.connectivity.orientations = self.connectivity.orientations[:dim]
        self.connectivity.cortical = self.connectivity.cortical[:dim]
        self.connectivity.hemisphere = self.connectivity.hemisphere[:dim]

    @property
    def integrator_dict(self):
        return {"integrator": self.simulator.integrator.__class__.__name__,
                 "dt": self.simulator.integrator.dt,
                 "noise": self.simulator.integrator.noise.__class__.__name__,
                 "noise_strength": self.simulator.integrator.noise.nsig}

    def write_tvb_simulator(self):
        self.write_group(self.simulator.connectivity, "connectivity", "connectivity", close_file=False)
        self.write_group(self.simulator.model.__dict__, "tvb_model", "dictionary", close_file=False)
        self.write_group(self.integrator_dict, "integrator", "dictionary", close_file=True)

    def prepare_connectivity(self):
        if os.path.isfile(self.connectivity_path):
            self.connectivity = Connectivity.from_file(self.connectivity_path)
        if self.force_dims is not None:
            self.force_dimensionality()
        if self.decouple:
            self.connectivity.weights *= 0.0
        else:
            self.connectivity.weights = self.connectivity.scaled_weights(mode="region")
            self.connectivity.weights /= np.percentile(self.connectivity.weights, 95)
        if not self.time_delays:
            self.connectivity.tract_lengths *= 0.0
        self.connectivity.configure()

    def prepare_simulator(self):
        self.prepare_connectivity()

        self.simulator = Simulator()

        self.simulator.connectivity = self.connectivity

        self.simulator.model = self.tvb_model(**self.model_params)

        self.simulator.integrator = self.integrator()
        self.simulator.integrator.dt = self.dt
        #                                            S_e,   S_i,  R_e, R_i
        self.simulator.integrator.noise.nsig = self.tvb_noise_strength

        mon_raw = Raw(period=self.simulator.integrator.dt)
        self.simulator.monitors = (mon_raw,)  # mon_bold, mon_eeg

        if self.plotter:
            self.plotter.plot_tvb_connectivity(self.simulator.connectivity)

        if self.writer:
            self.write_tvb_simulator()

    def write_nest_network(self):
        pass
        # for pop_params in pops_params:
        #     for self.nest_model_builder
        #
        # nest_network_dict = {""}
        # h5_file = \
        #     write_group(simulator.connectivity, "nest_population_model_params", "list_of_dictionaryies",
        #                 h5_file, writer, config, close_file=False)

    def prepare_nest_network(self):


        # Build a NEST network model with the corresponding builder
        # Using all default parameters for this example
        self.nest_model_builder = self.nest_model_builder(self.simulator, self.nest_nodes_ids, config=self.config)

        # ----------------------------------------------------------------------------------------------------------------
        # ----Uncomment below to modify the builder by changing the default options:--------------------------------------
        # ----------------------------------------------------------------------------------------------------------------

        self.nest_model_builder.population_order = self.nest_populations_order

        exc_pop_scale = 1.6
        inh_pop_scale = 0.4
        self.N_E = int(self.nest_model_builder.population_order * exc_pop_scale)
        self.N_I = int(self.nest_model_builder.population_order * inh_pop_scale)

        G = self.simulator.model.G[0]
        lamda = self.simulator.model.lamda[0]
        w_p = self.simulator.model.w_p[0]
        J_i = self.simulator.model.J_i[0]

        common_params = {
            "V_th": -50.0,  # mV
            "V_reset": -55.0,  # mV
            "E_L": -70.0,  # mV
            "E_ex": 0.0,  # mV
            "E_in": -70.0,  # mV
            "tau_decay_AMPA": 2.0,  # ms
            "tau_decay_GABA_A": 10.0,  # ms
            "tau_decay_NMDA": 100.0,  # ms
            "tau_rise_NMDA": 2.0,  # ms
            "s_AMPA_ext_max": self.N_E * np.ones((self.nest_model_builder.number_of_nodes,)).astype("f"),
            "epsilon": 1.0,
            "alpha": 0.5,  # kHz
            "beta": 0.062,
            "lambda_NMDA": 0.28,
            "I_e": 0.0  # pA
        }
        self.nest_model_builder.params_ex = dict(common_params)
        self.nest_model_builder.params_ex.update({
            "C_m": 500.0,  # pF
            "g_L": 25.0,  # nS
            "t_ref": 2.0,  # ms
            "g_AMPA_ext": 3.37,  # nS
            "g_AMPA": 0.065,  # nS
            "g_NMDA": 0.20,  # nS
            "g_GABA_A": 10.94,  # nS
            "w_E": w_p,  # w+ in the paper
            "w_I": J_i,
            "N_E": self.N_E,
            "N_I": self.N_I
        })
        self.nest_model_builder.params_in = dict(common_params)
        self.nest_model_builder.params_in.update({
            "C_m": 200.0,  # pF
            "g_L": 20.0,  # nS
            "t_ref": 1.0,  # ms
            "g_AMPA_ext": 2.59,  # nS
            "g_AMPA": 0.051,  # nS
            "g_NMDA": 0.16,  # nS
            "g_GABA_A": 8.51,  # nS
            "w_E": 1.0,
            "w_I": 1.0,
            "N_E": self.N_E,
            "N_I": self.N_I
        })

        def param_fun(node_index, params, lamda=1.0):
            w_E_ext = lamda * G * \
                      self.nest_model_builder.tvb_weights[:, list(self.nest_model_builder.spiking_nodes_ids).index(node_index)]
            w_E_ext[node_index] = 1.0  # this is external input weight to this node
            out_params = dict(params)
            out_params.update({"w_E_ext": w_E_ext})
            return out_params

        # Populations' configurations
        # When any of the properties model, params and scale below depends on regions,
        # set a handle to a function with
        # arguments (region_index=None) returning the corresponding property
        self.nest_model_builder.populations = [
            {"label": "E", "model": "iaf_cond_deco2014",
             "nodes": None,  # None means "all"
             "params": lambda node_index: param_fun(node_index, self.nest_model_builder.params_ex),
             "scale": exc_pop_scale},
            {"label": "I", "model": "iaf_cond_deco2014",
             "nodes": None,  # None means "all"
             "params": lambda node_index: param_fun(node_index, self.nest_model_builder.params_in,
                                                    lamda=lamda),
             "scale": inh_pop_scale}
        ]

        # Within region-node connections
        # When any of the properties model, conn_spec, weight, delay, receptor_type below
        # set a handle to a function with
        # arguments (region_index=None) returning the corresponding property
        self.nest_model_builder.populations_connections = [
            #              ->
            {"source": "E", "target": "E",  # E -> E This is a self-connection for population "E"
             "model": "static_synapse",
             "conn_spec": self.nest_model_builder.config.DEFAULT_CONNECTION["conn_spec"],
             "weight": 1.0,
             "delay": 0.1,
             "receptor_type": 0, "nodes": None},  # None means "all"
            {"source": "E", "target": "I",  # E -> I
             "model": "static_synapse",
             "conn_spec": self.nest_model_builder.config.DEFAULT_CONNECTION["conn_spec"],
             "weight": 1.0,
             "delay": 0.1,
             "receptor_type": 0, "nodes": None},  # None means "all"
            {"source": "I", "target": "E",  # I -> E
             "model": "static_synapse",
             "conn_spec": self.nest_model_builder.config.DEFAULT_CONNECTION["conn_spec"],
             "weight": -1.0,
             "delay": 0.1,
             "receptor_type": 0, "nodes": None},  # None means "all"
            {"source": "I", "target": "I",  # I -> I This is a self-connection for population "I"
             "model": "static_synapse",
             "conn_spec": self.nest_model_builder.config.DEFAULT_CONNECTION["conn_spec"],
             "weight": -1.0,
             "delay": 0.1,
             "receptor_type": 0, "nodes": None}  # None means "all"
        ]

        # Among/Between region-node connections
        # Given that only the AMPA population of one region-node couples to
        # all populations of another region-node,
        # we need only one connection type

        # When any of the properties model, conn_spec, weight, delay, receptor_type below
        # depends on regions, set a handle to a function with
        # arguments (source_region_index=None, target_region_index=None)
        self.nest_model_builder.nodes_connections = []
        if len(self.nest_nodes_ids) > 1:
            self.nest_model_builder.nodes_connections = [
                #              ->
                {"source": "E", "target": ["E"],
                 "model": "static_synapse",
                 "conn_spec": self.nest_model_builder.config.DEFAULT_CONNECTION["conn_spec"],
                 #  weight scaling the TVB connectivity weight
                 "weight": 1.0,
                 # additional delay to the one of TVB connectivity
                 "delay": self.nest_model_builder.tvb_delay,
                 # Each region emits spikes in its own port:
                 "receptor_type": self.nest_model_builder.receptor_by_source_region,
                 "source_nodes": None, "target_nodes": None}  # None means "all"
            ]
            if self.nest_model_builder.tvb_model.lamda[0] > 0:
                self.nest_model_builder.nodes_connections[0]["target"] += ["I"]

        # Creating  devices to be able to observe NEST activity:
        # Labels have to be different
        self.nest_model_builder.output_devices = []
        connections = OrderedDict({})
        #          label <- target population
        connections["E"] = "E"
        connections["I"] = "I"
        self.nest_model_builder.output_devices.append(
            {"model": "spike_detector", "params": {},
             "connections": connections, "nodes": None})  # None means "all"

        connections = OrderedDict({})
        #               label    <- target population
        connections["Excitatory"] = "E"
        connections["Inhibitory"] = "I"
        params = dict(self.nest_model_builder.config.NEST_OUTPUT_DEVICES_PARAMS_DEF["multimeter"])
        params["interval"] = self.nest_model_builder.nest_instance.GetKernelStatus("resolution")
        params['record_from'] = ["V_m",
                                 "s_AMPA", "x_NMDA", "s_NMDA", "s_GABA",
                                 "I_AMPA", "I_NMDA", "I_GABA", "I_L", "I_e",
                                 "spikes_exc", "spikes_inh"
                                 ]
        for i_node in range(self.nest_model_builder.number_of_nodes):
            params['record_from'].append("s_AMPA_ext_%d" % i_node)
            params['record_from'].append("I_AMPA_ext_%d" % i_node)
            params['record_from'].append("spikes_exc_ext_%d" % i_node)

        self.nest_model_builder.output_devices.append(
            {"model": "multimeter", "params": params,
             "connections": connections, "nodes": None})  # None means "all"

        connections = OrderedDict({})
        #          label <- target population
        connections["Stimulus"] = ["E", "I"]
        self.nest_model_builder.input_devices = [
            {"model": "poisson_generator",
             "params": {"rate": self.nest_stimulus_rate, "origin": 0.0,
                        "start": 0.1,
                        # "stop": 100.0
                        },
             "connections": connections, "nodes": None,
             "weights": 1.0, "delays": 0.0,
             "receptor_types": lambda target_node_id: int(target_node_id + 1)}
        ]

        # ----------------------------------------------------------------------------------------------------------------
        # ----------------------------------------------------------------------------------------------------------------
        # ----------------------------------------------------------------------------------------------------------------

        if self.writer:
            self.write_nest_network()

        self.nest_network = self.nest_model_builder.build_spiking_network()

        return self.nest_network

    def prepare_rate_interface(self, lamda):
        from tvb_multiscale.spiking_models.builders.templates \
            import tvb_delay, receptor_by_source_region
        # For spike transmission from TVB to NEST devices acting as TVB proxy nodes with TVB delays:
        self.interface_builder.tvb_to_spikeNet_interfaces = [
            {"model": "inhomogeneous_poisson_generator",
             "params": {"allow_offgrid_times": False},
             # # ---------Properties potentially set as function handles with args (nest_node_id=None)-------------------------
             "interface_weights": 1.0 * self.N_E,
             # Applied outside NEST for each interface device
             # -------Properties potentially set as function handles with args (tvb_node_id=None, nest_node_id=None)-----------
             #   To multiply TVB connectivity weight:
             "weights": 1.0,
             #                                     To add to TVB connectivity delay:
             "delays": lambda tvb_node_id, nest_node_id:
             tvb_delay(tvb_node_id, nest_node_id, self.interface_builder.tvb_delays),
             "receptor_types": lambda tvb_node_id, nest_node_id:
             receptor_by_source_region(tvb_node_id, nest_node_id, start=1),
             # --------------------------------------------------------------------------------------------------------------
             #             TVB sv -> NEST population
             "connections": {"R_e": ["E"]},
             "source_nodes": None, "target_nodes": None}]  # None means all here

        if lamda > 0.0:
            #       Coupling towards the inhibitory population as well:
            self.interface_builder.tvb_to_spikeNet_interfaces[0]["connections"]["R_e"] += ["I"]

        return self.interface_builder

    def prepare_dc_interface(self, G, lamda):
        from tvb_multiscale.spiking_models.builders.templates \
            import random_normal_tvb_weight, tvb_delay

        # For injecting current to NEST neurons via dc generators acting as TVB proxy nodes with TVB delays:
        self.interface_builder.tvb_to_spikeNet_interfaces = [
            {"model": "dc_generator", "params": {},
             # ---------Properties potentially set as function handles with args (nest_node_id=None)---------------------------
             #   Applied outside NEST for each interface device
             "interface_weights": 1.0 * self.N_E,
             # -------Properties potentially set as function handles with args (tvb_node_id=None, nest_node_id=None)-----------
             #    To multiply TVB connectivity weight:
             "weights": lambda tvb_node_id, nest_node_id:
             random_normal_tvb_weight(tvb_node_id, nest_node_id,
                                      G * self.interface_builder.tvb_weights,
                                      sigma=0.1),
             #    To add to TVB connectivity delay:
             "delays": lambda tvb_node_id, nest_node_id:
             tvb_delay(tvb_node_id, nest_node_id, self.interface_builder.tvb_delays),
             # ----------------------------------------------------------------------------------------------------------------
             #    TVB sv -> NEST population
             "connections": {"S_e": ["E"]},
             "source_nodes": None, "target_nodes": None}]  # None means all here

        if lamda > 0.0:
            # Coupling to inhibitory populations as well (feedforward inhibition):
            self.interface_builder.tvb_to_spikeNet_interfaces.append(
                {"model": "dc_generator", "params": {},
                 # ---------Properties potentially set as function handles with args (nest_node_id=None)---------------------------
                 #   Applied outside NEST for each interface device
                 "interface_weights": 1.0 * self.N_E,
                 # -------Properties potentially set as function handles with args (tvb_node_id=None, nest_node_id=None)-----------
                 #    To multiply TVB connectivity weight:
                 "weights": lambda tvb_node_id, nest_node_id:
                 random_normal_tvb_weight(tvb_node_id, nest_node_id,
                                          G * lamda *
                                          self.interface_builder.tvb_weights, sigma=0.1),
                 #    To add to TVB connectivity delay:
                 "delays": lambda tvb_node_id, nest_node_id:
                 tvb_delay(tvb_node_id, nest_node_id, self.interface_builder.tvb_delays),
                 # ----------------------------------------------------------------------------------------------------------------
                 #    TVB sv -> NEST population
                 "connections": {"S_e": ["I"]},
                 "source_nodes": None, "target_nodes": None}
            )

        return self.interface_builder

    def prepare_Ie_interface(self, lamda):
        from tvb_multiscale.spiking_models.builders.templates \
            import random_normal_tvb_weight, tvb_delay

        # For directly setting an external current parameter in NEST neurons instantaneously:
        self.interface_builder.tvb_to_spikeNet_interfaces = [
            {"model": "current", "parameter": "I_e",
             # ---------Properties potentially set as function handles with args (nest_node_id=None)---------------------------
             "interface_weights": 1.0 * self.N_E,
             # ----------------------------------------------------------------------------------------------------------------
             #                  TVB sv -> NEST population
             "connections": {"S_e": ["E"]},
             "nodes": None}]  # None means all here
        if self.interface_builder.tvb_model.lamda[0] > 0.0:
            # Coupling to inhibitory populations as well (feedforward inhibition):
            self.interface_builder.tvb_to_spikeNet_interfaces.append(
                {
                    "model": "current", "parameter": "I_e",
                    # ---------Properties potentially set as function handles with args (nest_node_id=None)---------------------------
                    "interface_weights": 1.0 * self.N_E * lamda,
                    # ----------------------------------------------------------------------------------------------------------------
                    #                     TVB sv -> NEST population
                    "connections": {"S_e": ["I"]},
                    "nodes": None}
            )

        return self.interface_builder

    def prepare_interface(self):

        G = self.simulator.model.G[0]
        lamda = self.simulator.model.lamda[0]

        # Build a TVB-NEST interface with all the appropriate connections between the
        # TVB and NEST modelled regions
        # Using all default parameters for this example
        self.interface_builder = self.interface_builder(self.simulator, self.nest_network,
                                                        self.nest_nodes_ids, self.exclusive_nodes, self.N_E)

        # ----------------------------------------------------------------------------------------------------------------
        # ----Uncomment below to modify the builder by changing the default options:--------------------------------------
        # ----------------------------------------------------------------------------------------------------------------

        # TVB -> NEST
        if self.interface == "rate":
            self.interface_builder = self.prepare_rate_interface(lamda)
        elif self.interface == "dc":
            self.interface_builder = self.prepare_dc_interface(G, lamda)
        elif self.interface == "Ie":
            self.interface_builder = self.prepare_Ie_interface(lamda)

        self.tvb_nest_model = self.interface_builder.build_interface()

        return self.tvb_nest_model

    def get_nest_data(self):

        self.nest_ts = \
            TimeSeriesRegion(
                self.nest_network.get_data_from_multimeter(mode="per_neuron"),
                connectivity=self.connectivity)[self.transient:]

        self.nest_spikes = self.nest_network.get_spikes(mode="events",
                                                        return_type="Series",
                                                        exclude_times=[0.0, self.transient])

        return self.nest_ts, self.nest_spikes

    def cosimulate(self, cleanup=True):

        # Configure the simulator with the TVB-NEST interface...
        self.simulator.configure(self.tvb_nest_model)

        # ...and simulate!
        results = self.simulator.run(simulation_length=self.simulation_length)

        # Integrate NEST one more NEST time step so that multimeters get the last time point
        # unless you plan to continue simulation later
        self.simulator.run_spiking_simulator(
            self.simulator.tvb_spikeNet_interface.nest_instance.GetKernelStatus("resolution"))
        # Clean-up NEST simulation
        self.simulator.tvb_spikeNet_interface.nest_instance.Cleanup()

        self.tvb_ts = TimeSeriesRegion(results[0][1], time=results[0][0],
                                       connectivity=self.simulator.connectivity,
                                       labels_ordering=["Time", "State Variable", "Region", "Neurons"],
                                       labels_dimensions={
                                          "State Variable": ["S_e", "S_i", "R_e", "R_i"],
                                          "Region": self.simulator.connectivity.region_labels.tolist()},
                                       sample_period=self.simulator.integrator.dt)

        if self.tvb_nest_model is not None:
            self.nest_ts, self.nest_spikes = self.get_nest_data()

        return self.nest_ts, self.nest_spikes, self.tvb_ts

    def simulate_nest(self, cleanup=True):
        self.nest_network.Prepare()
        self.nest_network.Run(self.simulation_length)
        self.nest_ts, self.nest_spikes = self.get_nest_data()
        if cleanup:
            self.nest_network.nest_instance.Cleanup()
        return self.nest_ts, self.nest_spikes

    def get_nest_rates(self):
        rates = []
        pop_labels = []
        for pop_label, pop_spikes in self.nest_spikes.iteritems():
            pop_labels.append(pop_label)
            rates.append([])
            reg_labels = []
            for reg_label, reg_spikes in pop_spikes.iteritems():
                reg_labels.append(reg_label)
                rates[-1].append(len(reg_spikes) / self.duration)

        self.rates["NEST"] = DataArray(np.array(rates),
                                       dims=["Population", "Region"],
                                       coords={"Population": pop_labels, "Region": reg_labels})
        return self.rates["NEST"]

    def get_tvb_rates(self, tvb_rates):
         self.rates["TVB"] = DataArray(tvb_rates.mean(axis=0).squeeze(),
                                       dims=tvb_rates.dims[1:3],
                                       coords={tvb_rates.dims[1]: tvb_rates.coords[tvb_rates.dims[1]],
                                               tvb_rates.dims[2]: tvb_rates.coords[tvb_rates.dims[2]]})
         return self.rates["TVB"]

    def plot_tvb_ts(self):
        # For raster plot:
        self.tvb_ts.plot_raster(plotter=self.plotter, per_variable=True, figsize=(10, 5))

        # For timeseries plot:
        self.tvb_ts.plot_timeseries(plotter=self.plotter, per_variable=True, figsize=(10, 5))

        spiking_nodes_ids = []
        if self.exclusive_nodes:
            spiking_nodes_ids = self.nest_nodes_ids

        if len(spiking_nodes_ids) > 0:
            self.tvb_ts[:, :, spiking_nodes_ids].plot_raster(plotter=self.plotter, per_variable=True, figsize=(10, 5),
                                                             figname="Spiking nodes TVB Time Series Raster")
            self.tvb_ts[:, :, spiking_nodes_ids].plot_timeseries(plotter=self.plotter, per_variable=True,
                                                                 figsize=(10, 5),
                                                                 figname="Spiking nodes TVB Time Series")

    def compute_nest_mean_field(self):
        labels_ordering = list(self.nest_ts.labels_ordering)
        labels_dimensions = dict(self.nest_ts.labels_dimensions)
        try:
            labels_ordering.remove("Neuron")
        except:
            pass
        try:
            del labels_dimensions["Neuron"]
        except:
            pass
        for dim in labels_ordering:
            labels_dimensions[dim] = self.nest_ts.coords[dim]
        mean_field = TimeSeriesRegion(self.nest_ts._data.mean(axis=-1), connectivity=self.nest_ts.connectivity,
                                      labels_ordering=labels_ordering, labels_dimensions=labels_dimensions,
                                      title="Mean field spiking nodes time series")

        # We place here all variables that relate to local excitatory synapses
        mean_field_exc = mean_field[:, ["spikes_exc", "s_AMPA", "I_AMPA", "x_NMDA", "s_NMDA", "I_NMDA"]]
        mean_field_exc._data.name = "Mean excitatory synapse data from NEST multimeter"

        # We place here all variables that relate to local inhibitory synapses
        mean_field_inh = mean_field[:, ["spikes_inh", "s_GABA", "I_GABA"]]
        mean_field_inh._data.name = "Mean inhibitory synapse data from NEST multimeter"

        # TODO: deal specifically with external input of node I to synapse I

        # Substitute the per-region synaptic variables with their sums to reduce outpumean_field:
        s_AMPA_ext_nest_nodes = \
            mean_field[:, ['s_AMPA_ext_%d' % node_id for node_id in [0] + self.nest_nodes_ids]]._data
        I_AMPA_ext_nest_nodes = \
            mean_field[:, ['I_AMPA_ext_%d' % node_id for node_id in [0] + self.nest_nodes_ids]]._data
        spikes_exc_ext_nest_nodes = mean_field[:,
                                    ['spikes_exc_ext_%d' % node_id for node_id in [0] + self.nest_nodes_ids]]._data

        s_AMPA_ext_tot = \
            mean_field[:, ['s_AMPA_ext_%d' % node_id for node_id in range(self.number_of_regions)]]. \
                _data.sum(axis=1).expand_dims(axis=1, dim={"Variable": ["s_AMPA_ext_tot"]})
        I_AMPA_ext_tot = \
            mean_field[:, ['I_AMPA_ext_%d' % node_id for node_id in range(self.number_of_regions)]]. \
                _data.sum(axis=1).expand_dims(axis=1, dim={"Variable": ["I_AMPA_ext_tot"]})
        spikes_exc_ext_tot = \
            mean_field[:, ['spikes_exc_ext_%d' % node_id for node_id in range(self.number_of_regions)]]. \
                _data.sum(axis=1).expand_dims(axis=1, dim={"Variable": ["spikes_exc_ext_tot"]})

        # We place here all variables that relate to large-scale excitatory synapses
        mean_field_ext = TimeSeriesRegion(
            concat([spikes_exc_ext_tot, s_AMPA_ext_tot, I_AMPA_ext_tot,
                    spikes_exc_ext_nest_nodes, s_AMPA_ext_nest_nodes, I_AMPA_ext_nest_nodes], "Variable"),
            connectivity=mean_field.connectivity)
        mean_field_ext._data.name = "Mean external synapse data from NEST multimeter"

        # We place here all variables that refer to all neurons of all populations the same
        mean_field_neuron = mean_field[:, ["I_e", "V_m", "I_L"]]

        return mean_field, mean_field_ext, mean_field_exc, mean_field_inh, mean_field_neuron

    def plot_nest_ts(self):
        mean_field, mean_field_ext, mean_field_exc, mean_field_inh, mean_field_neuron = self.compute_nest_mean_field()

        mean_field_ext.plot_timeseries(plotter=self.plotter, per_variable=True, figsize=(10, 5))
        # Then plot the local excitatory...:
        mean_field_exc.plot_timeseries(plotter=self.plotter, per_variable=True, figsize=(10, 5))
        # ...and local inhibtiory synaptic activity that result:
        mean_field_inh.plot_timeseries(plotter=self.plotter, per_variable=True, figsize=(10, 5))
        # ...and finally the common neuronal variables:
        mean_field_neuron.plot_timeseries(plotter=self.plotter, per_variable=True, figsize=(10, 5))

        mean_field_ext.plot_raster(plotter=self.plotter, per_variable=True, figsize=(10, 5))
        # Then plot the local excitatory...:
        mean_field_exc.plot_raster(plotter=self.plotter, per_variable=True, figsize=(10, 5))
        # ...and local inhibtiory synaptic activity that result:
        mean_field_inh.plot_raster(plotter=self.plotter, per_variable=True, figsize=(10, 5))
        # ...and finally the common neuronal variables:
        mean_field_neuron.plot_raster(plotter=self.plotter, per_variable=True, figsize=(10, 5))

        self.plotter.plot_spike_events(self.nest_spikes)

    def run(self, **model_params):

        if self.writer:
           self.write_general_params(close_file=False)
           self.write_model_params()

        # ----------------------1. Define a TVB simulator (model, integrator, monitors...)----------------------------------
        print("Preparing TVB simulator...")
        self.prepare_simulator()

        # ------2. Build the NEST network model (fine-scale regions' nodes, stimulation devices, spike_detectors etc)-------

        print("Building NEST network...")
        tic = time.time()
        self.prepare_nest_network()
        print("Done! in %f min" % ((time.time() - tic) / 60))

        # -----------------------------------3. Build the TVB-NEST interface model -----------------------------------------
        if self.interface is not None:
            print("Building TVB-NEST interface...")
            tic = time.time()
            self.prepare_interface()
            print("Done! in %f min" % ((time.time() - tic) / 60))
        else:
            self.tvb_nest_model = None

        # -----------------------------------4. Simulate and gather results-------------------------------------------------
        t_start = time.time()
        if self.tvb_nest_model is not None or len(self.nest_nodes_ids) == 0:
            self.cosimulate()

        else:
            self.simulate_nest()
        print("\nSimulated in %f secs!" % (time.time() - t_start))

        self.duration = self.simulation_length - self.transient

        # -----------------------------------5. Compute rate per region and population--------------------------------------
        self.rates = {}
        if self.nest_spikes is not None:
            self.rates["NEST"] = self.get_nest_rates()
        if self.tvb_ts is not None:
            self.rates["TVB"] = self.get_tvb_rates(self.tvb_ts[:, ["R_e", "R_i"]].squeeze())

        # -------------------------------------------5. Plot results--------------------------------------------------------
        if self.plotter:
            self.plot_tvb_ts()
            self.plot_nest_ts()

        return self.rates