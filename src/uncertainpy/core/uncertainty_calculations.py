import numpy as np
import multiprocess as mp
from tqdm import tqdm
import chaospy as cp

from .run_model import RunModel
from .base import ParameterBase

# Model is now potentially set two places, is that a problem?
class UncertaintyCalculations(ParameterBase):
    def __init__(self,
                 model=None,
                 parameters=None,
                 features=None,
                 CPUs=mp.cpu_count(),
                 suppress_model_graphics=True,
                 p=3,
                 nr_collocation_nodes=None,
                 quadrature_order=4,
                 nr_mc_samples=10*3,
                 nr_pc_mc_samples=10*5,
                 seed=None,
                 allow_incomplete=False
                 verbose_level="info",
                 verbose_filename=None,):

        self.runmodel = RunModel(model=model,
                                 parameters=parameters,
                                 features=features,
                                 verbose_level=verbose_level,
                                 verbose_filename=verbose_filename,
                                 CPUs=CPUs,
                                 suppress_model_graphics=suppress_model_graphics)


        super(UncertaintyCalculations, self).__init__(parameters=parameters,
                                                      model=model,
                                                      features=features,
                                                      verbose_level=verbose_level,
                                                      verbose_filename=verbose_filename)

        self.nr_collocation_nodes = nr_collocation_nodes
        self.quadrature_order = quadrature_order
        self.nr_mc_samples = nr_mc_samples
        self.nr_pc_mc_samples = nr_pc_mc_samples
        self.p = p

        self.P = None
        self.distribution = None
        self.data = None
        self.U_hat = {}
        self.U_mc = {}

        self.allow_incomplete = allow_incomplete


        if seed is not None:
            np.random.seed(seed)


    @ParameterBase.features.setter
    def features(self, new_features):
        ParameterBase.features.fset(self, new_features)

        self.runmodel.features = self.features


    @ParameterBase.model.setter
    def model(self, new_model):
        ParameterBase.model.fset(self, new_model)

        self.runmodel.model = self.model


    @ParameterBase.parameters.setter
    def parameters(self, new_parameters):
        ParameterBase.parameters.fset(self, new_parameters)

        self.runmodel.parameters = self.parameters



    def create_distribution(self, uncertain_parameters=None):
        if self.parameters.distribution is None:
            uncertain_parameters = self.convert_uncertain_parameters(uncertain_parameters)

            parameter_distributions = self.parameters.get("distribution", uncertain_parameters)

            distribution = cp.J(*parameter_distributions)
        else:
            distribution = self.parameters.distribution

        return distribution



    def create_mask(self, nodes, feature, weights=None):
        if feature not in self.data:
            raise AttributeError("Error: {} is not a feature".format(feature))

        masked_values = []
        mask = np.ones(len(self.data[feature]["values"]), dtype=bool)

        # TODO use numpy masked array
        for i, result in enumerate(self.data[feature]["values"]):
            if np.any(np.isnan(result)):
                mask[i] = False
            else:
                masked_values.append(result)


        if len(nodes.shape) > 1:
            masked_nodes = nodes[:, mask]
        else:
            masked_nodes = nodes[mask]

        if weights is not None:
            # TODO is this needed?
            if len(weights.shape) > 1:
                masked_weights = weights[:, mask]
            else:
                masked_weights = weights[mask]

        if not np.all(mask):
            self.logger.warning("Feature: {} only yields ".format(feature) +
                                "results for {}/{} ".format(sum(mask), len(mask)) +
                                "parameter combinations.")


        if weights is None:
            return np.array(masked_nodes), np.array(masked_values), mask
        else:
            return np.array(masked_nodes), np.array(masked_values), np.array(masked_weights), mask



    def convert_uncertain_parameters(self, uncertain_parameters):
        if isinstance(uncertain_parameters, str):
            uncertain_parameters = [uncertain_parameters]

        if self.parameters.distribution is not None:
            if uncertain_parameters is None:
                uncertain_parameters = self.parameters.get("name")
            elif sorted(uncertain_parameters) != sorted(self.parameters.get("name")):
                 raise ValueError("A common multivariate distribution is given, " +
                                  "and all uncertain parameters must be used. " +
                                  "Set uncertain_parameters to None or a list of all " +
                                  "uncertain parameters.")
        else:
            if uncertain_parameters is None:
                uncertain_parameters = self.parameters.get_from_uncertain("name")

        return uncertain_parameters



    # TODO not tested
    def create_PCE_spectral(self, uncertain_parameters=None):
        uncertain_parameters = self.convert_uncertain_parameters(uncertain_parameters)

        self.distribution = self.create_distribution(uncertain_parameters=uncertain_parameters)

        self.P = cp.orth_ttr(self.p, self.distribution)

        nodes, weights = cp.generate_quadrature(self.quadrature_order, self.distribution, rule="J", sparse=True)

        # Running the model
        self.data = self.runmodel.run(nodes, uncertain_parameters)

        # Calculate PC for each feature
        for feature in tqdm(self.data,
                            desc="Calculating PC for each feature",
                            total=len(self.data)):
            masked_nodes, masked_values, masked_weights, mask = self.create_mask(nodes, feature, weights)

            if (np.all(mask) or self.allow_incomplete) and sum(mask) > 0:
                self.U_hat[feature] = cp.fit_quadrature(self.P, masked_nodes,
                                                        masked_weights, masked_values)
            else:
                self.logger.warning("Uncertainty quantification is not performed " +\
                                    "for feature: {} ".format(feature) +\
                                    "due too not all parameter combinations " +\
                                    "giving a result. Set allow_incomplete=True to " +\
                                    "calculate the uncertainties anyway.")

            if not np.all(mask):
                self.data.incomplete.append(feature)



    def create_PCE_collocation(self, uncertain_parameters=None):
        uncertain_parameters = self.convert_uncertain_parameters(uncertain_parameters)

        self.distribution = self.create_distribution(uncertain_parameters=uncertain_parameters)

        self.P = cp.orth_ttr(self.p, self.distribution)

        if self.nr_collocation_nodes is None:
            self.nr_collocation_nodes = 2*len(self.P) + 2

        nodes = self.distribution.sample(self.nr_collocation_nodes, "M")

        # Running the model
        self.data = self.runmodel.run(nodes, uncertain_parameters)


        # Calculate PC for each feature
        for feature in tqdm(self.data,
                            desc="Calculating PC for each feature",
                            total=len(self.data)):
            masked_nodes, masked_values, mask = self.create_mask(nodes, feature)

            if (np.all(mask) or self.allow_incomplete) and sum(mask) > 0:
                self.U_hat[feature] = cp.fit_regression(self.P, masked_nodes,
                                                        masked_values, rule="T")
            else:
                self.logger.warning("Uncertainty quantification is not performed " +
                                    "for feature: {} ".format(feature) +
                                    "due too not all parameter combinations " +
                                    "giving a result. Set allow_incomplete=True to " +
                                    "calculate the uncertainties anyway.")


            if not np.all(mask):
                self.data.incomplete.append(feature)



    # TODO not tested
    def create_PCE_spectral_rosenblatt(self, uncertain_parameters=None):
        uncertain_parameters = self.convert_uncertain_parameters(uncertain_parameters)

        self.distribution = self.create_distribution(uncertain_parameters=uncertain_parameters)


        # Create the Multivariat normal distribution
        dist_MvNormal = []
        for parameter in uncertain_parameters:
            dist_MvNormal.append(cp.Normal())

        dist_MvNormal = cp.J(*dist_MvNormal)

        self.P = cp.orth_ttr(self.p, dist_MvNormal)

        nodes_MvNormal, weights_MvNormal = cp.generate_quadrature(self.quadrature_order, dist_MvNormal,
                                                                  rule="J", sparse=True)
        # TODO Is this correct, copy pasted from below.
        nodes = self.distribution.inv(dist_MvNormal.fwd(nodes_MvNormal))
        weights = weights_MvNormal*self.distribution.pdf(nodes)/dist_MvNormal.pdf(nodes_MvNormal)

        self.distribution = dist_MvNormal

        # Running the model
        self.data = self.runmodel.run(nodes, uncertain_parameters)


        # Calculate PC for each feature
        for feature in tqdm(self.data,
                            desc="Calculating PC for each feature",
                            total=len(self.data)):
            masked_nodes, masked_values, masked_weights, mask = self.create_mask(nodes_MvNormal,
                                                                      feature,
                                                                      weights)


            if (np.all(mask) or self.allow_incomplete) and sum(mask) > 0:
                self.U_hat[feature] = cp.fit_quadrature(self.P, masked_nodes,
                                                        masked_weights,
                                                        masked_values)
            else:
                self.logger.warning("Uncertainty quantification is not performed " +
                                    "for feature: {} ".format(feature) +
                                    "due too not all parameter combinations " +
                                    "giving a result. Set allow_incomplete=True to " +
                                    "calculate the uncertainties anyway.")

            if not np.all(mask):
                self.data.incomplete.append(feature)



    def create_PCE_collocation_rosenblatt(self, uncertain_parameters=None):
        uncertain_parameters = self.convert_uncertain_parameters(uncertain_parameters)

        self.distribution = self.create_distribution(uncertain_parameters=uncertain_parameters)


        # Create the Multivariat normal distribution
        # dist_MvNormal = cp.Iid(cp.Normal(), len(uncertain_parameters))
        dist_MvNormal = []
        for parameter in uncertain_parameters:
            dist_MvNormal.append(cp.Normal())

        dist_MvNormal = cp.J(*dist_MvNormal)


        self.P = cp.orth_ttr(self.p, dist_MvNormal)

        if self.nr_collocation_nodes is None:
            self.nr_collocation_nodes = 2*len(self.P) + 2

        nodes_MvNormal = dist_MvNormal.sample(self.nr_collocation_nodes, "M")
        nodes = self.distribution.inv(dist_MvNormal.fwd(nodes_MvNormal))

        self.distribution = dist_MvNormal

        # Running the model
        self.data = self.runmodel.run(nodes, uncertain_parameters)

        # Calculate PC for each feature
        for feature in tqdm(self.data,
                            desc="Calculating PC for each feature",
                            total=len(self.data)):
            masked_nodes, masked_values, mask = self.create_mask(nodes_MvNormal, feature)




            if (np.all(mask) or self.allow_incomplete) and sum(mask) > 0:
                self.U_hat[feature] = cp.fit_regression(self.P, masked_nodes,
                                                        masked_values, rule="T")
            else:
                self.logger.warning("Uncertainty quantification is not performed " +
                                    "for feature: {} ".format(feature) +
                                    "due too not all parameter combinations " +
                                    "giving a result. Set allow_incomplete=True to " +
                                    "calculate the uncertainties anyway.")

            if not np.all(mask):
                self.data.incomplete.append(feature)


    def analyse_PCE(self):
        if len(self.data.uncertain_parameters) == 1:
            self.logger.info("Only 1 uncertain parameter. Sensitivity is not calculated")

        for feature in self.data:
            if feature in self.U_hat:
                self.data[feature]["mean"] = cp.E(self.U_hat[feature], self.distribution)
                self.data[feature]["variance"] = cp.Var(self.U_hat[feature], self.distribution)

                samples = self.distribution.sample(self.nr_pc_mc_samples, "H")

                if len(self.data.uncertain_parameters) > 1:
                    self.U_mc[feature] = self.U_hat[feature](*samples)

                    self.data[feature]["sensitivity_1"] = cp.Sens_m(self.U_hat[feature], self.distribution)
                    self.data[feature]["sensitivity_t"] = cp.Sens_t(self.U_hat[feature], self.distribution)
                    self.calculate_sensitivity_sum(sensitivity="sensitivity_1")
                    self.calculate_sensitivity_sum(sensitivity="sensitivity_t")

                else:
                    self.U_mc[feature] = self.U_hat[feature](samples)

                self.data[feature]["percentile_5"] = np.percentile(self.U_mc[feature], 5, -1)
                self.data[feature]["percentile_95"] = np.percentile(self.U_mc[feature], 95, -1)





    def create_PCE_custom(self, uncertain_parameters=None):
        raise NotImplementedError("Custom Polynomial Chaos Expansion method not implemented")


    def custom_uncertainty_quantification(self, **kwargs):
        raise NotImplementedError("Custom uncertainty calculation method not implemented")


    def polynomial_chaos(self,
                         uncertain_parameters=None,
                         method="collocation",
                         rosenblatt=False):
        uncertain_parameters = self.convert_uncertain_parameters(uncertain_parameters)

        if method == "collocation":
            if rosenblatt:
                self.create_PCE_collocation_rosenblatt(uncertain_parameters)
            else:
                self.create_PCE_collocation(uncertain_parameters)

        elif method == "spectral":
            if rosenblatt:
                self.create_PCE_spectral_rosenblatt(uncertain_parameters)
            else:
                self.create_PCE_spectral(uncertain_parameters)

        elif method == "custom":
            self.create_PCE_custom(uncertain_parameters)

        # TODO add support for more methods here by using
        # try:
        #     getattr(self, method)
        # except AttributeError:
        #     raise NotImplementedError("{} not implemented".format{method})

        else:
            raise ValueError("No polynomial chaos method with name {}".format(method))

        self.analyse_PCE()

        return self.data


    def monte_carlo(self, uncertain_parameters=None):
        uncertain_parameters = self.convert_uncertain_parameters(uncertain_parameters)

        self.distribution = self.create_distribution(uncertain_parameters=uncertain_parameters)

        nodes = self.distribution.sample(self.nr_mc_samples, "M")

        self.data = self.runmodel.run(nodes, uncertain_parameters)


        # TODO mask data

        for feature in self.data:
            self.data[feature]["mean"] = np.mean(self.data[feature]["values"], 0)
            self.data[feature]["variance"] = np.var(self.data[feature]["values"], 0)

            self.data[feature]["percentile_5"] = np.percentile(self.data[feature]["values"], 5, 0)
            self.data[feature]["percentile_95"] = np.percentile(self.data[feature]["values"], 95, 0)

        return self.data


    def calculate_sensitivity_sum(self, sensitivity="sensitivity_1"):
        if sensitivity not in ["sensitivity_1", "sensitivity_t", "1", "t"]:
            raise ValueError("Sensitivity must be either: sensitivity_1, sensitivity_t, 1, or t.")

        if sensitivity == "1":
            sensitivity = "sensitivity_1"

        if sensitivity == "t":
            sensitivity = "sensitivity_t"

        for feature in self.data:
            if sensitivity in self.data[feature]:
                total_sensitivity = 0
                total_sense = []
                for i in range(0, len(self.data.uncertain_parameters)):
                    tmp_sum_sensitivity = np.sum(self.data[feature][sensitivity][i])

                    total_sensitivity += tmp_sum_sensitivity
                    total_sense.append(tmp_sum_sensitivity)

                for i in range(0, len(self.data.uncertain_parameters)):
                    if total_sensitivity != 0:
                        total_sense[i] /= float(total_sensitivity)

                self.data[feature][sensitivity + "_sum"] = np.array(total_sense)