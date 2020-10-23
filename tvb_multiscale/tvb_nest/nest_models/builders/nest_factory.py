# -*- coding: utf-8 -*-

import os
import sys
import shutil

import numpy as np

from tvb_multiscale.tvb_nest.config import CONFIGURED, initialize_logger
from tvb_multiscale.tvb_nest.nest_models.devices import NESTInputDeviceDict, NESTOutputDeviceDict
from tvb_multiscale.core.spiking_models.builders.factory import log_path

from tvb.contrib.scripts.utils.log_error_utils import raise_value_error, warning
from tvb.contrib.scripts.utils.data_structures_utils import ensure_list
from tvb.contrib.scripts.utils.file_utils import safe_makedirs


LOG = initialize_logger(__name__)


#TODO: Find a better way to abstract between nest_factory and factory!


# Helper functions with NEST


def load_nest(config=CONFIGURED, logger=LOG):
    """This method will load a NEST instance and return it, after reading the NEST environment constants.
        Arguments:
         config: configuration class instance. Default: imported default CONFIGURED object.
         logger: logger object. Default: local LOG object.
        Returns:
         the imported NEST instance
    """
    logger.info("Loading a NEST instance...")
    nest_path = config.NEST_PATH
    os.environ['NEST_INSTALL_DIR'] = nest_path
    log_path('NEST_INSTALL_DIR', logger)
    os.environ['NEST_DATA_DIR'] = os.path.join(nest_path, "share/nest")
    log_path('NEST_DATA_DIR', logger)
    os.environ['NEST_DOC_DIR'] = os.path.join(nest_path, "share/doc/nest")
    log_path('NEST_DOC_DIR', logger)
    os.environ['NEST_MODULE_PATH'] = os.path.join(nest_path, "lib/nest")
    log_path('NEST_MODULE_PATH', logger)
    os.environ['PATH'] = os.path.join(nest_path, "bin") + ":" + os.environ['PATH']
    log_path('PATH', logger)
    LD_LIBRARY_PATH = os.environ.get('LD_LIBRARY_PATH', '')
    if len(LD_LIBRARY_PATH) > 0:
        LD_LIBRARY_PATH = ":" + LD_LIBRARY_PATH
    os.environ['LD_LIBRARY_PATH'] = os.environ['NEST_MODULE_PATH'] + LD_LIBRARY_PATH
    log_path('LD_LIBRARY_PATH', logger)
    os.environ['SLI_PATH'] = os.path.join(os.environ['NEST_DATA_DIR'], "sli")
    log_path('SLI_PATH', logger)

    os.environ['NEST_PYTHON_PREFIX'] = config.PYTHON
    log_path('NEST_PYTHON_PREFIX', logger)
    sys.path.insert(0, os.environ['NEST_PYTHON_PREFIX'])
    logger.info("%s: %s" % ("system path", sys.path))

    import nest
    return nest


def compile_modules(modules, recompile=False, config=CONFIGURED, logger=LOG):
    """Function to compile NEST modules.
       Arguments:
        modules: a sequence (list, tuple) of NEST modules' names (strings).
        recompile: (bool) flag to recompile a module that is already compiled. Default = False.
        config: configuration class instance. Default: imported default CONFIGURED object.
        logger: logger object. Default: local LOG object.
    """
    # ...unless we need to first compile it:
    logger.info("Preparing MYMODULES_BLD_DIR: %s" % config.MYMODULES_BLD_DIR)
    safe_makedirs(config.MYMODULES_BLD_DIR)
    lib_path = os.path.join(os.environ["NEST_INSTALL_DIR"], "lib", "nest")
    include_path = os.path.join(os.environ["NEST_INSTALL_DIR"], "include")
    for module in ensure_list(modules):
        modulemodule = module + "module"
        module_bld_dir = os.path.join(config.MYMODULES_BLD_DIR, module)
        solib_file = os.path.join(module_bld_dir, modulemodule + ".so")
        dylib_file = os.path.join(module_bld_dir, "lib" + modulemodule + ".dylib")
        installed_solib_file = os.path.join(lib_path, os.path.basename(solib_file))
        installed_dylib_file = os.path.join(lib_path, os.path.basename(dylib_file))
        module_include_path = os.path.join(include_path, modulemodule)
        installed_h_file = os.path.join(module_include_path, modulemodule + ".h")
        if not os.path.isfile(solib_file) or not os.path.isfile(dylib_file) or recompile:
            # If the .so file or the .dylib file don't exist, or if the user requires recompilation,
            # proceed with recompilation:
            if not os.path.exists(module_bld_dir):
                # If there is no module build directory at all,
                # create one and copy there the source files:
                source_path = os.path.join(config.MYMODULES_DIR, module)
                logger.info("Copying module sources from %s\ninto %s..." % (source_path, module_bld_dir))
                shutil.copytree(source_path, module_bld_dir)
            # Now compile:
            logger.info("Compiling %s..." % module)
            logger.info("in build directory %s..." % module_bld_dir)
            success_message = "DONE compiling and installing %s!" % module
            from pynestml.frontend.pynestml_frontend import install_nest
            install_nest(module_bld_dir, config.NEST_PATH)
            logger.info("Compiling finished without errors...")
        else:
            logger.info("Installing precompiled module %s..." % module)
            success_message = "DONE installing precompiled module %s!" % module
            # Just copy the .h, .so, and .dylib files to the appropriate NEST build paths:
            shutil.copyfile(solib_file, installed_solib_file)
            shutil.copyfile(solib_file, installed_dylib_file)
            safe_makedirs(include_path)
            shutil.copyfile(os.path.join(module_bld_dir, modulemodule + ".h"), installed_h_file)
        if os.path.isfile(installed_solib_file) and \
                os.path.isfile(installed_dylib_file) and \
                    os.path.isfile(installed_h_file):
            logger.info(success_message)
        else:
            logger.warn("Something seems to have gone wrong with compiling and/or installing %s!"
                        "\n No %s, %s or %s file found!"
                        % (module, installed_solib_file, installed_dylib_file, installed_h_file))


def get_populations_neurons(population, inds_fun=None):
    """This method will return a subset NEST.NodeCollection instance
       of the NESTPopulation._population, if inds_fun argument is a function
       Arguments:
        population: a NESTPopulation class instance
        inds_fun: a function that takes a NEST.NodeCollection as argument and returns another NEST.NodeCollection
       Returns:
        NEST.NodeCollection NESTPopulation._population instance
    """
    if inds_fun is None:
        return population._population
    return inds_fun(population._population)


def create_conn_spec(n_src=1, n_trg=1, src_is_trg=False, config=CONFIGURED, **kwargs):
    """This function returns a conn_spec dictionary and the expected/accurate number of total connections.
       Arguments:
        n_src: number (int) of source neurons. Default = 1.
        n_trg: number (int) of target neurons. Default = 1.
        src_is_trg: a (bool) flag to determine if the source and target populations are the same one. Default = False.
        config: configuration class instance. Default: imported default CONFIGURED object.
    """
    conn_spec = dict(config.DEFAULT_CONNECTION["conn_spec"])
    P_DEF = conn_spec["p"]
    conn_spec.update(kwargs)
    rule = conn_spec["rule"]
    p = conn_spec["p"]
    N = conn_spec["N"]
    autapses = conn_spec["allow_autapses"]
    multapses = conn_spec["allow_multapses"]
    indegree = conn_spec["indegree"]
    outdegree = conn_spec["outdegree"]
    conn_spec = {
        'rule': rule,
        'allow_autapses': autapses,  # self-connections flag
        'allow_multapses': multapses  # multiple connections per neurons' pairs flag
    }
    if rule == 'one_to_one':
        # TODO: test whether there is an error
        # if Nsrc != Ntrg in this case
        # and if src_is_trg and autapses or multapses play a role
        return conn_spec, np.minimum(n_src, n_trg)
    elif rule == 'fixed_total_number':
        if N is None:
            # Assume all to all if N is not given:
            N = n_src * n_trg
            if p is not None:
                # ...prune to end up to connection probability p if p is given
                N = int(np.round(p * N))
        conn_spec['N'] = N
        return conn_spec, N
    elif rule == 'fixed_indegree':
        if indegree is None:
            # Compute indegree following connection probability p if not given
            if p is None:
                p = P_DEF
            indegree = int(np.round(p * n_src))
        conn_spec['indegree'] = indegree
        return conn_spec, indegree * n_trg
    elif rule == 'fixed_outdegree':
        if outdegree is None:
            # Compute outdegree following connection probability p if not given
            if p is None:
                p = P_DEF
            outdegree = int(np.round(p * n_trg))
        conn_spec['outdegree'] = outdegree
        return conn_spec, outdegree * n_src
    else:
        Nall = n_src * n_trg
        if src_is_trg and autapses is False:
            Nall -= n_src
        if rule == 'pairwise_bernoulli':
            if p is None:
                p = P_DEF
            conn_spec['p'] = p
            return conn_spec, int(np.round(p * Nall))
        else:  # assuming rule == 'all_to_all':
            return conn_spec, Nall


def device_to_dev_model(device):
    """Method to return a multimeter device for a spike_multimeter model name."""
    if device == "spike_multimeter":
        return "multimeter"
    else:
        return device


def create_device(device_model, params=None, config=CONFIGURED, nest_instance=None):
    """Method to create a NESTDevice.
       Arguments:
        device_model: name (string) of the device model
        params: dictionary of parameters of device and/or its synapse. Default = None
        config: configuration class instance. Default: imported default CONFIGURED object.
        nest_instance: the NEST instance.
                       Default = None, in which case we are going to load one, and also return it in the output
       Returns:
        the NESTDevice class, and optionally, the NEST instance if it is loaded here.
    """
    if nest_instance is None:
        nest_instance = load_nest(config=config)
        return_nest = True
    else:
        return_nest = False
    # Assert the model name...
    device_model = device_to_dev_model(device_model)
    if device_model in NESTInputDeviceDict.keys():
        devices_dict = NESTInputDeviceDict
        default_params_dict = config.NEST_INPUT_DEVICES_PARAMS_DEF
    elif device_model in NESTOutputDeviceDict.keys():
        devices_dict = NESTOutputDeviceDict
        default_params_dict = config.NEST_OUTPUT_DEVICES_PARAMS_DEF
    else:
        raise_value_error("%s is neither one of the available input devices: %s\n "
                          "nor of the output ones: %s!" %
                          (device_model, str(config.NEST_INPUT_DEVICES_PARAMS_DEF),
                           str(config.NEST_OUTPUT_DEVICES_PARAMS_DEF)))
    default_params = dict(default_params_dict.get(device_model, {}))
    if isinstance(params, dict) and len(params) > 0:
        default_params.update(params)
    # TODO: a better solution for the strange error with inhomogeneous poisson generator
    label = default_params.pop("label", "")
    try:
        nest_device_id = nest_instance.Create(device_model, params=default_params)
    except:
        warning("Using temporary hack for creating successive %s devices!" % device_model)
        nest_device_id = nest_instance.Create(device_model, params=default_params)
    nest_device = devices_dict[device_model](nest_device_id, nest_instance, label=label)
    if return_nest:
        return nest_device, nest_instance
    else:
        return nest_device


def connect_device(nest_device, population, neurons_inds_fun, weight=1.0, delay=0.0, receptor_type=0,
                   nest_instance=None, config=CONFIGURED):
    """This method connects a NESTDevice to a NESTPopulation instance.
       Arguments:
        nest_device: the NESTDevice instance
        population: the NESTPopulation instance
        neurons_inds_fun: a function to return a NESTPopulation or a subset thereof of the target population.
                          Default = None.
        weight: the weights of the connection. Default = 1.0.
        delay: the delays of the connection. Default = 0.0.
        receptor_type: type of the synaptic receptor. Default = 0.
        config: configuration class instance. Default: imported default CONFIGURED object.
        nest_instance: instance of NEST. Default = None, in which case the one of the nest_device is used.
       Returns:
        the connected NESTDevice
    """
    if receptor_type is None:
        receptor_type = 0
    if nest_instance is None:
        raise_value_error("There is no NEST instance!")
    resolution = nest_instance.GetKernelStatus("resolution")
    if isinstance(delay, dict):
        if delay["low"] < resolution:
            delay["low"] = resolution
            warning("Minimum delay %f is smaller than the NEST simulation resolution %f!\n"
                    "Setting minimum delay equal to resolution!" % (delay["low"], resolution))
        if delay["high"] <= delay["low"]:
            raise_value_error("Maximum delay %f is not smaller than minimum one %f!" % (delay["high"], delay["low"]))
    else:
        if delay < resolution:
            delay = resolution
            warning("Delay %f is smaller than the NEST simulation resolution %f!\n"
                    "Setting minimum delay equal to resolution!" % (delay, resolution))
    syn_spec = {"weight": weight, "delay": delay, "receptor_type": receptor_type}
    neurons = get_populations_neurons(population, neurons_inds_fun)
    if nest_device.model == "spike_recorder":
        #                     source  ->  target
        nest_instance.Connect(neurons, nest_device.device, syn_spec=syn_spec)
    else:
        nest_instance.Connect(nest_device.device, neurons, syn_spec=syn_spec)
    return nest_device
