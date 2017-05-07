from pyspark import SparkContext, SparkConf, SparkFiles
import pystan
import numpy as np
from pystan import StanModel
import pickle

def consensus_avg(J):
    def c(f1, f2):
        if np.isnan(f1).any():
            return f2

        # initialize fs
        fs = [f1, f2]

        # following Scott '16.
        # weights are optimal for Gaussian
        sigma_j = [np.linalg.inv(np.cov(fs[j])) for j in [0, 1]]

        return [sigma_j[0] + sigma_j[1],
                        np.dot(sigma_j[0], f1) + np.dot(sigma_j[1], f2)]
    return c

def concatenate_samples(a, b):
    return np.vstack((a, b))

class Stark:
    rdd = None
    n_partitions = None
    prepare_data_callback = None

    def __init__(self, context, rdd, prepare_data_callback):
        self.rdd = rdd
        self.context = context
        self.prepare_data_callback = prepare_data_callback
        self.n_partitions = self.rdd.getNumPartitions()

    def setStanModel(self, **kwargs):
        sm = StanModel(**kwargs)
        self.smpickled = pickle.dumps(sm)

    def _mcmc(self, callback, **kwargs):
        smpickled = self.smpickled
        def w(sts):
            ## do MCMC...
            sts = list(sts)
            data = callback(sts)
            sm = pickle.loads(smpickled)
            fit = sm.sampling(data=data, **kwargs)
            h = [np.array(samples[1]) for samples in fit.extract().items()]
            for prm in h:
                if len(prm.shape)==1:
                    prm.shape = (prm.shape[0], 1)
            # the singleton array is b/c we want to keep the matrix
            # together, not broken apart into rows
            # Transpose into variables-by-samples
            return [np.transpose(np.hstack(h))]
        return w

    def concensusWeight(self, **kwargs):
        if not "iter" in kwargs:
          kwargs["iter"] = 2000
        if not "chains" in kwargs:
          kwargs["chains"] = 1
        kwargs["n_jobs"] = 1
        subposteriors = self.rdd.mapPartitions(self._mcmc(self.prepare_data_callback, **kwargs))
        concensusProducts = subposteriors.reduce(consensus_avg(self.n_partitions))
        consensusSamples = np.dot(
            np.linalg.inv(concensusProducts[0]), # inverse of sum of the W_s
            concensusProducts[1] # sum of (W_s theta_s)
        )
        return consensusSamples

    def distribute(self, n=2, **kwargs):
        if not "iter" in kwargs:
          kwargs["iter"] = 2000
        if not "chains" in kwargs:
          kwargs["chains"] = 1
        kwargs["n_jobs"] = 1
        self.rdd_distribute = self.rdd.coalesce(1)
        single_rdd = self.rdd_distribute
        for i in range(n-1):
            self.rdd_distribute = self.rdd.union(single_rdd)
        posteriors = self.rdd_distribute.mapPartitions(self._mcmc(self.prepare_data_callback, **kwargs))

        return posteriors.reduce(concatenate_samples)
