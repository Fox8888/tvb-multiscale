# -*- coding: utf-8 -*-
import numpy as np

from tvb_multiscale.tvb_nest.config import CONFIGURED, initialize_logger
from tvb_multiscale.tvb_nest.nest_models.population import NESTPopulation
from tvb_multiscale.tvb_nest.nest_models.region_node import NESTRegionNode
from tvb_multiscale.tvb_nest.nest_models.brain import NESTBrain
from tvb_multiscale.tvb_nest.nest_models.network import NESTNetwork
from tvb_multiscale.tvb_nest.nest_models.builders.nest_factory import \
    load_nest, compile_modules, get_populations_neurons, create_conn_spec, create_device, connect_device
from tvb_multiscale.core.spiking_models.builders.factory import build_and_connect_devices
from tvb_multiscale.core.spiking_models.builders.base import SpikingModelBuilder

from tvb.contrib.scripts.utils.log_error_utils import raise_value_error
from tvb.contrib.scripts.utils.data_structures_utils import ensure_list


LOG = initialize_logger(__name__)


class NESTModelBuilder(SpikingModelBuilder):

    """This is the base class of a NESTModelBuilder,
       which builds a NESTNetwork from user configuration inputs.
       The builder is half way opionionated.
    """

    config = CONFIGURED
    nest_instance = None
    default_min_spiking_dt = CONFIGURED.NEST_MIN_DT
    default_min_delay = CONFIGURED.NEST_MIN_DT
    modules_to_install = []
    _spiking_brain = NESTBrain()

    def __init__(self, nest_nodes_ids, nest_instance=None, config=CONFIGURED, logger=LOG, **tvb_params):
        super(NESTModelBuilder, self).__init__(nest_nodes_ids, config, logger, **tvb_params)
        # Setting or loading a nest instance:
        if nest_instance is not None:
            self.nest_instance = nest_instance
        else:
            self.nest_instance = load_nest(self.config, self.logger)

        self._spiking_brain = NESTBrain()

        # Setting NEST defaults from config
        self.default_population = {"model": self.config.DEFAULT_MODEL, "scale": 1, "params": {}, "nodes": None}

        self.default_synaptic_weight_scaling = \
            lambda weight, n_cons: self.config.DEFAULT_SPIKING_SYNAPTIC_WEIGHT_SCALING(weight, n_cons)

        self.default_populations_connection = dict(self.config.DEFAULT_CONNECTION)
        self.default_populations_connection["delay"] = self.default_min_delay
        self.default_populations_connection["nodes"] = None

        self.default_nodes_connection = dict(self.config.DEFAULT_CONNECTION)
        self.default_nodes_connection["delay"] = self.default_populations_connection["delay"]
        self.default_nodes_connection.update({"source_nodes": None, "target_nodes": None})

        self.default_devices_connection = dict(self.config.DEFAULT_CONNECTION)
        self.default_devices_connection["delay"] = self.default_min_delay
        self.default_devices_connection["nodes"] = None

    def _configure_nest_kernel(self):
        self.nest_instance.ResetKernel()  # This will restart NEST!
        self._update_spiking_dt()
        self._update_default_min_delay()
        self.nest_instance.set_verbosity(self.config.NEST_VERBOCITY)  # don't print all messages from NEST
        self.nest_instance.SetKernelStatus({"resolution": self.spiking_dt, "print_time": self.config.NEST_PRINT_TIME})

    def _compile_install_nest_module(self, module):
        """This method will try to install the input NEST module.
           If it fails, it will try to compile it first and retry installing it.
           Arguments:
            module: the name (string) of the module to be installed and, possibly, compiled
        """
        if module[-6:] == "module":
            module_name = module.split("module")[0]
        else:
            module_name = module
            module = module + "module"
        try:
            # Try to install it...
            self.logger.info("Trying to install module %s..." % module)
            self.nest_instance.Install(module)
            self.logger.info("DONE installing module %s!" % module)
        except:
            self.logger.info("FAILED! We need to first compile it!")
            # ...unless we need to first compile it:
            compile_modules(module_name, recompile=False, config=self.config)
            # and now install it...
            self.logger.info("Installing now module %s..." % module)
            self.nest_instance.Install(module)
            self.logger.info("DONE installing module %s!" % module)

    def compile_install_nest_modules(self, modules_to_install):
        """This method will try to install the input NEST modules, also compiling them, if necessary.
            Arguments:
             modules_to_install: a sequence (list, tuple) of the names (strings)
                                 of the modules to be installed and, possibly, compiled
        """
        if len(modules_to_install) > 0:
            self.logger.info("Starting to compile modules %s!" % str(modules_to_install))
            while len(modules_to_install) > 0:
                self._compile_install_nest_module(modules_to_install.pop())

    def confirm_compile_install_nest_models(self, models):
        """This method will try to confirm the existence of the input NEST models,
           and if they don't exist, it will try to install them,
           and possibly compile them, by determining the modules' names from the ones of the models.
           Arguments:
            models: a sequence (list, tuple) of the names (strings)
                    of the models to be confirmed, and/or installed and, possibly, compiled
        """
        nest_models = self.nest_instance.Models()
        models = ensure_list(models)
        for model in models:  # , module # zip(models, cycle(modules_to_install)):
            if model not in nest_models:
                self._compile_install_nest_module(model)

    def configure(self):
        self._configure_nest_kernel()
        super(NESTModelBuilder, self).configure()
        self.compile_install_nest_modules(self.modules_to_install)
        self.confirm_compile_install_nest_models(self._models)

    def build_spiking_population(self, label, model, size, params):
        """This methods builds a NESTPopulation instance,
           which represents a population of spiking neurons of the same neural model,
           and residing at a particular brain region node.
           Arguments:
            label: name (string) of the population
            model: name (string) of the neural model
            size: number (integer) of the neurons of this population
            params: dictionary of parameters of the neural model to be set upon creation
           Returns:
            a NESTPopulation class instance
        """
        return NESTPopulation(self.nest_instance.Create(model, int(np.round(size)), params=params),
                              label, model, self.nest_instance)

    @property
    def min_delay(self):
        try:
            return self.nest_instance.GetKernelStatus("min_delay")
        except:
            return self.default_min_delay

    def _get_minmax_delay(self, delay, minmax):
        """A method to get the minimum or maximum delay from a distribution dictionary."""
        if isinstance(delay, dict):
            if "distribution" in delay.keys():
                if delay["distribution"] == "uniform":
                    return delay[minmax]
                else:
                    raise_value_error("Only uniform distribution is allowed for delays to make sure that > min_delay!\n"
                                      "Distribution given is %s!" % delay["distribution"])
            else:
                raise_value_error("If delay is a dictionary it has to be a distribution dictionary!\n"
                                  "Instead, the delay given is %s\n" % str(delay))
        else:
            return delay

    def _get_min_delay(self, delay):
        return self._get_minmax_delay(delay, "low")

    def _get_max_delay(self, delay):
        return self._get_minmax_delay(delay, "high")

    def _assert_synapse_model(self, synapse_model, delay):
        """A method to assert the synapse_model (default = "static_synapse), in combination with the delay value.
           It is based on respecting the fact that rate_connection_instantaneous requires a delay of zero.
        """
        if synapse_model is None:
            synapse_model = "static_synapse"
        if synapse_model.find("rate") > -1:
            if synapse_model == "rate_connection_instantaneous" and delay != 0.0:
                raise_value_error("Coupling neurons with rate_connection_instantaneous synapse "
                                  "and delay = %s != 0.0 is not possible!" % str(delay))
            elif self._get_min_delay(delay) == 0.0 and synapse_model == "rate_connection_delayed":
                raise_value_error("Coupling neurons with rate_connection_delayed synapse "
                                  "and delay = %s <= 0.0 is not possible!" % str(delay))
            elif self._get_max_delay(delay) == 0.0:
                return "rate_connection_instantaneous"
            else:
                return "rate_connection_delayed"
        else:
            return synapse_model

    def _assert_delay(self, delay, synapse_model="static_synapse"):
        """A method to assert the delay value, in combination with the synapse_model.
           It is based on respecting the minimum possible delay of the network,
           as well as the fact that rate_connection_instantaneous requires a delay of zero.
        """
        if synapse_model.find("rate") > -1:
            if synapse_model == "rate_connection_instantaneous" and delay != 0.0:
                raise_value_error("Coupling neurons with rate_connection_instantaneous synapse "
                                  "and delay = %s != 0.0 is not possible!" % str(delay))
            elif synapse_model == "rate_connection_delayed" and self._get_min_delay(delay) <= 0.0:
                raise_value_error("Coupling neurons with rate_connection_delayed synapse "
                                  "and delay = %s <= 0.0 is not possible!" % str(delay))
            elif self._get_min_delay(delay) < 0.0:
                raise_value_error("Coupling rate neurons with negative delay = %s < 0.0 is not possible!" % str(delay))
        elif self._get_min_delay(delay) < self.spiking_dt:
            raise_value_error("Coupling spiking neurons with delay = %s < NEST integration step = %f is not possible!:"
                              "\n" % (str(delay), self.spiking_dt))
        return delay

    def _prepare_conn_spec(self, pop_src, pop_trg, conn_spec):
        return create_conn_spec(n_src=pop_src.number_of_neurons, n_trg=pop_trg.number_of_neurons,
                                src_is_trg=(pop_src.population == pop_trg.population),
                                config=self.config, **conn_spec)[0]

    def set_synapse(self, syn_model, weight, delay, receptor_type, params={}):
        """Method to set the synaptic model, the weight, the delay,
           the synaptic receptor type, and other possible synapse parameters
           to a synapse_params dictionary.
           Arguments:
            - syn_model: the name (string) of the synapse model
            - weight: the weight of the synapse
            - delay: the delay of the connection,
            - receptor_type: the receptor type
            - params: a dict of possible synapse parameters
           Returns:
            a dictionary of the whole synapse configuration
        """
        syn_spec = {'synapse_model': syn_model, 'weight': weight, 'delay': delay, 'receptor_type': receptor_type}
        syn_spec.update(params)
        return syn_spec

    def _prepare_syn_spec(self, syn_spec):
        # Prepare the parameters of synapses:
        syn_spec["synapse_model"] = self._assert_synapse_model(syn_spec.get("synapse_model",
                                                                            syn_spec.get("model", "static_synapse")),
                                                               syn_spec["delay"])
        # Scale the synaptic weight with respect to the total number of connections between the two populations:
        if syn_spec["synapse_model"] == "rate_connection_instantaneous":
            del syn_spec["delay"]  # For instantaneous rate connections
        else:
            syn_spec["delay"] = self._assert_delay(syn_spec["delay"])
        return syn_spec

    def connect_two_populations(self, pop_src, src_inds_fun, pop_trg, trg_inds_fun, conn_spec, syn_spec):
        """Method to connect two NESTPopulation instances in the SpikingNetwork.
           Arguments:
            source: the source NESTPopulation of the connection
            src_inds_fun: a function that selects a subset of the souce population neurons
            target: the target NESTPopulation of the connection
            trg_inds_fun: a function that selects a subset of the target population neurons
            conn_params: a dict of parameters of the connectivity pattern among the neurons of the two populations,
                         excluding weight and delay ones
            synapse_params: a dict of parameters of the synapses among the neurons of the two populations,
                            including weight, delay and synaptic receptor type ones
        """
        # Prepare the parameters of connectivity:
        conn_spec = self._prepare_conn_spec(pop_src, pop_trg, conn_spec)
        # Prepare the parameters of the synapse:
        syn_spec = self._prepare_syn_spec(syn_spec)
        # We might create the same connection multiple times for different synaptic receptors...
        receptors = ensure_list(syn_spec["receptor_type"])
        for receptor in receptors:
            syn_spec["receptor_type"] = receptor
            self.nest_instance.Connect(get_populations_neurons(pop_src, src_inds_fun),
                                       get_populations_neurons(pop_trg, trg_inds_fun),
                                       conn_spec, syn_spec)

    def build_spiking_region_node(self, label="", input_node=None, *args, **kwargs):
        """This methods builds a NESTRegionNode instance,
           which consists of a pandas.Series of all SpikingPopulation instances,
           residing at a particular brain region node.
           Arguments:
            label: name (string) of the region node. Default = ""
            input_node: an already created SpikingRegionNode() class. Default = None.
            *args, **kwargs: other optional positional or keyword arguments
           Returns:
            a SpikingRegionNode class instance
        """
        return NESTRegionNode(label, input_node, self.nest_instance)

    def build_and_connect_devices(self, devices):
        """Method to build and connect input or output devices, organized by
           - the variable they measure or stimulate (pandas.Series), and the
           - population(s) (pandas.Series), and
           - brain region nodes (pandas.Series) they target.
           See tvb_multiscale.core.spiking_models.builders.factory
           and tvb_multiscale.tvb_nest.nest_models.builders.nest_factory"""
        return build_and_connect_devices(devices, create_device, connect_device,
                                         self._spiking_brain, self.config, nest_instance=self.nest_instance)

    def build(self):
        """A method to build the final NESTNetwork class based on the already created constituents."""
        return NESTNetwork(self.nest_instance, self._spiking_brain,
                           self._output_devices, self._input_devices, config=self.config)
