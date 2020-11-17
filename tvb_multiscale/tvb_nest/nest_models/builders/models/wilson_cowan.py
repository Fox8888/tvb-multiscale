# -*- coding: utf-8 -*-

from tvb_multiscale.tvb_nest.config import CONFIGURED
from tvb_multiscale.tvb_nest.nest_models.builders.models.default_exc_io_inh_i import \
    DefaultExcIOInhIBuilder, DefaultExcIOInhIMultisynapseBuilder


class WilsonCowanBuilder(DefaultExcIOInhIBuilder):

    def __init__(self, nest_nodes_ids, nest_instance=None, config=CONFIGURED, set_defaults=True, **tvb_params):
        super(WilsonCowanBuilder, self).__init__(nest_nodes_ids, nest_instance, config, **tvb_params)

        # self.w_ee = self.weight_fun(self.tvb_model.c_ee[0].item())
        # self.w_ei = self.weight_fun(self.tvb_model.c_ei[0].item())
        # self.w_ie = self.weight_fun(-self.tvb_model.c_ie[0].item())
        # self.w_ii = self.weight_fun(-self.tvb_model.c_ii[0].item())

        if set_defaults:
            self.set_defaults()


class WilsonCowanMultisynapseBuilder(DefaultExcIOInhIMultisynapseBuilder):

    def __init__(self, tvb_simulator, nest_nodes_ids, nest_instance=None, config=CONFIGURED, set_defaults=True,
                 E_ex=0.0, E_in=-85.0, tau_syn_ex=0.2, tau_syn_in=2.0, **tvb_params):

        super(WilsonCowanMultisynapseBuilder, self).__init__(
            nest_nodes_ids, nest_instance, config, set_defaults=False,
            E_ex=E_ex, E_in=E_in, tau_syn_ex=tau_syn_ex, tau_syn_in=tau_syn_in, **tvb_params)

        self.default_population["model"] = "aeif_cond_alpha_multisynapse"

        self.w_ee = self.weight_fun(self.tvb_model.c_ee[0].item())
        self.w_ei = self.weight_fun(self.tvb_model.c_ei[0].item())
        self.w_ie = self.weight_fun(self.tvb_model.c_ie[0].item())
        self.w_ii = self.weight_fun(self.tvb_model.c_ii[0].item())

        if set_defaults:
            self.set_defaults()
