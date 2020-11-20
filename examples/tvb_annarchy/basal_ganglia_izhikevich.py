# -*- coding: utf-8 -*-
import time
from six import string_types

import numpy as np

from tvb.basic.profile import TvbProfile
TvbProfile.set_profile(TvbProfile.LIBRARY_PROFILE)

from tvb_multiscale.tvb_annarchy.config import CONFIGURED, Config
from tvb_multiscale.tvb_annarchy.annarchy_models.builders.models.basal_ganglia_izhikevich \
    import BasalGangliaIzhikevichBuilder
# from tvb_multiscale.tvb_annarchy.interfaces.builders.models.red_ww_basal_ganglia_izhikevich \
#     import RedWWexcIOBuilder as BasalGangliaRedWWexcIOBuilder
from tvb_multiscale.core.tvb.simulator_builder import SimulatorBuilder
from tvb_multiscale.core.plot.plotter import Plotter
from examples.plot_write_results import plot_write_results

from tvb.datatypes.connectivity import Connectivity
from tvb.simulator.models.reduced_wong_wang_exc_io import ReducedWongWangExcIO


def results_path_fun(annarchy_model_builder, tvb_annarchy_builder, tvb_to_annarchy_mode="rate", annarchy_to_tvb=True,
                     config=None):
    if config is None:
        if tvb_annarchy_builder is not None:
            tvb_annarchy_builder_str = "_" + tvb_annarchy_builder.__name__.split("Builder")[0] + \
                                   np.where(isinstance(tvb_to_annarchy_mode, string_types),
                                             "_" + str(tvb_to_annarchy_mode), "").item()
        else:
            tvb_annarchy_builder_str = ""
        return os.path.join(CONFIGURED.out.FOLDER_RES.split("/res")[0],
                            annarchy_model_builder.__name__.split("Builder")[0] +
                            tvb_annarchy_builder_str +
                            np.where(annarchy_to_tvb, "_bidir", "").item()
                            )
    else:
        return config.out.FOLDER_RES


def main_example(tvb_sim_model, annarchy_model_builder, tvb_annarchy_builder,
                 annarchy_nodes_ids, annarchy_populations_order=100,
                 tvb_to_annarchy_mode="rate", annarchy_to_tvb=True, exclusive_nodes=True,
                 connectivity=CONFIGURED.DEFAULT_CONNECTIVITY_ZIP, delays_flag=True,
                 simulation_length=110.0, transient=10.0, variables_of_interest=None,
                 config=None, plot_write=True, **model_params):

    if config is None:
        config = Config(
                    output_base=results_path_fun(annarchy_model_builder, tvb_annarchy_builder, tvb_to_annarchy_mode,
                                                 annarchy_to_tvb, config))

    plotter = Plotter(config)

    # ----------------------1. Define a TVB simulator (model, integrator, monitors...)----------------------------------
    simulator_builder = SimulatorBuilder()
    # Optionally modify the default configuration:
    simulator_builder.model = tvb_sim_model
    simulator_builder.variables_of_interest = variables_of_interest
    simulator_builder.connectivity = connectivity
    simulator_builder.delays_flag = delays_flag
    simulator = simulator_builder.build(**model_params)

    # ------2. Build the annarchy network model (fine-scale regions' nodes, stimulation devices, spike_detectors etc)-------

    print("Building annarchy network...")
    tic = time.time()

    # Build a annarchy network model with the corresponding builder
    # Using all default parameters for this example
    annarchy_model_builder = annarchy_model_builder(simulator, annarchy_nodes_ids, config=config)
    annarchy_model_builder.population_order = annarchy_populations_order
    populations = []
    populations_sizes = []
    for pop in annarchy_model_builder.populations:
        populations.append(pop["label"])
        populations_sizes.append(int(np.round(pop["scale"] * annarchy_model_builder.population_order)))
    # Common order of neurons' number per population:
    annarchy_network = annarchy_model_builder.build_spiking_network()
    annarchy_network.configure()
    print(annarchy_network.print_str(connectivity=True))
    print("Done! in %f min" % ((time.time() - tic) / 60))

    # -----------------------------------3. Build the TVB-annarchy interface model -----------------------------------------

    if tvb_annarchy_builder:
        print("Building TVB-annarchy interface...")
        tic = time.time()
        # Build a TVB-annarchy interface with all the appropriate connections between the
        # TVB and annarchy modelled regions
        # Using all default parameters for this example
        tvb_annarchy_builder = tvb_annarchy_builder(simulator, annarchy_network, annarchy_nodes_ids, exclusive_nodes,
                                                    populations_sizes=populations_sizes[0])
        tvb_annarchy_model = tvb_annarchy_builder.build_interface(tvb_to_annarchy_mode=tvb_to_annarchy_mode, annarchy_to_tvb=annarchy_to_tvb)
        print(tvb_annarchy_model.print_str(detailed_output=True, connectivity=False))
        print("Done! in %f min" % ((time.time() - tic)/60))

        # -----------------------------------4. Simulate and gather results-------------------------------------------------

        # Configure the simulator with the TVB-annarchy interface...
        simulator.configure(tvb_annarchy_model)
        # ...and simulate!
        t_start = time.time()
        results = simulator.run(simulation_length=simulation_length)
        # Integrate annarchy one more annarchy time step so that multimeters get the last time point
        # unless you plan to continue simulation later
        simulator.run_spiking_simulator(simulator.tvb_spikeNet_interface.annarchy_instance.GetKernelStatus("resolution"))
        # Clean-up annarchy simulation
        simulator.tvb_spikeNet_interface.annarchy_instance.Cleanup()
        print("\nSimulated in %f secs!" % (time.time() - t_start))

    else:
        # -----------------------------------4. Simulate and gather results---------------------------------------------
        print("Simulating ANNarchy only...")
        t_start = time.time()
        annarchy_network.Run(simulation_length)
        print("\nSimulated in %f secs!" % (time.time() - t_start))

        # -------------------------------------------5. Plot results--------------------------------------------------------
    if plot_write:
        try:
            plot_write_results(results, simulator, populations=populations, populations_sizes=populations_sizes,
                               transient=transient, tvb_state_variable_type_label="State Variables",
                               tvb_state_variables_labels=simulator.model.variables_of_interest,
                               plotter=plotter, config=config)
        except Exception as e:
            print("Error in plotting or writing to files!:\n%s" % str(e))

    return results, simulator


if __name__ == "__main__":

    annarchy_nodes_ids = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]

    import os
    home_path = os.path.join(os.getcwd().split("tvb-multiscale")[0], "tvb-multiscale")
    DATA_PATH = os.path.join(home_path, "examples/data")

    w = np.loadtxt(os.path.join(DATA_PATH, "./basal_ganglia_conn/conn_denis_weights.txt"))

    c = np.loadtxt(os.path.join(DATA_PATH, "./basal_ganglia_conn/aal_plus_BG_centers.txt"), usecols=range(1, 3))
    rl = np.loadtxt(os.path.join(DATA_PATH, "./basal_ganglia_conn/aal_plus_BG_centers.txt"), dtype="str", usecols=(0,))
    t = np.loadtxt(os.path.join(DATA_PATH, "./basal_ganglia_conn/BGplusAAL_tract_lengths.txt"))

    # Remove BG -> Cortex connections
    w[[0, 1, 2, 3, 6, 7], :][:, 10:] = 0.0
    connectivity = Connectivity(region_labels=rl, weights=w, centres=c, tract_lengths=t)

    tvb_model = ReducedWongWangExcIO  # ReducedWongWangExcIOInhI

    model_params = {}

    main_example(tvb_model, BasalGangliaIzhikevichBuilder, None,
                 annarchy_nodes_ids,  annarchy_populations_order=200,
                 tvb_to_annarchy_mode=None, annarchy_to_tvb=False, exclusive_nodes=True,  # "rate"
                 connectivity=connectivity, delays_flag=True,
                 simulation_length=110.0, transient=0.0,
                 variables_of_interest=None,
                 config=None, **model_params)