import os
import sys
import time
import tempfile
import copy

from collections import OrderedDict

import numpy

# larcv_fetcher can also do synthetic IO without any larcv installation
from . larcvio   import larcv_fetcher

import datetime
import pathlib


import logging
logger = logging.getLogger()

class trainercore(object):
    '''
    This class is the core interface for training.  Each function to
    be overridden for a particular interface is marked and raises
    a NotImplemented error.

    '''


    def __init__(self, args):

        self._iteration    = 0
        self._global_step  = 0
        self.args          = args
        if args.framework.name == "torch":
            sparse = args.framework.sparse
        else:
            sparse = False

        dataset = self.args.dataset

        # Constructing the larcv fetcher fixes the output mode (graph/sparse/etc)
        self.larcv_fetcher = larcv_fetcher.larcv_fetcher(
            mode            = args.mode.name,
            distributed     = args.run.distributed,
            dataset         = self.args.dataset,
            data_format     = self.args.network.data_format
        )



    def initialize(self, io_only=True):
        self._initialize_io(color=0)

    def _initialize_io(self, color=None):

        # If in training mode, we open the "train" file for sure.
        #    Open the validation file if possible.

        # If in inference mode, we open the "test" file.
        #   Eventually, will add output saving against the test file.
        # TODO: Add output support.

        def file_exists(filename, directory):
            return pathlib.Path(directory + filename).exists()




        # If mode is train, prepare the train file.
        if self.args.mode.name == "train":
            if not file_exists(self.args.dataset.train_file, self.args.dataset.data_directory):
                raise Exception(f"Can not open training file {self.args.dataset.train_file} in directory {self.args.dataset.data_directory}")

            # Prepare the training sample:
            self._train_data_size = self.larcv_fetcher.prepare_sample(
                name            = "primary",
                input_file      = self.args.dataset.data_directory + self.args.dataset.train_file,
                batch_size      = self.args.run.minibatch_size,
                color           = color
            )

            # If the validation file exists, load that too:
            if not file_exists(self.args.dataset.val_file, self.args.dataset.data_directory):
                self._val_data_size = None
                logger.info(f"Can not open validation file {self.args.dataset.val_file} in directory {self.args.dataset.data_directory} - skipping")

            else:
                self._val_data_size = self.larcv_fetcher.prepare_sample(
                    name            = "val",
                    input_file      = self.args.dataset.data_directory + self.args.dataset.val_file,
                    batch_size      = self.args.run.minibatch_size,
                    color           = color
                )

        elif self.args.mode.name == "inference":
            pass


    def init_network(self):
        pass

    def print_network_info(self):
        pass

    def set_compute_parameters(self):
        pass


    def log(self, metrics, kind):

        log_string = ""

        log_string += "{} Global Step {}: ".format(kind, self._global_step)


        for key in metrics:
            if key in self._log_keys and key != "global_step":
                log_string += "{}: {:.3}, ".format(key, metrics[key])

        if kind == "train":
            log_string += "Img/s: {:.2} ".format(metrics["images_per_second"])
            log_string += "IO: {:.2} ".format(metrics["io_fetch_time"])
        else:
            log_string.rstrip(", ")

        logger.info(log_string)

        return

    def on_step_end(self):
        pass

    def on_epoch_end(self):
        pass

    def metrics(self, metrics):
        # This function looks useless, but it is not.
        # It allows a handle to the distributed network to allreduce metrics.
        return metrics

    def stop(self):
        # Mostly, this is just turning off the io:
        # self._larcv_interface.stop()
        pass

    def close_savers(self):
        pass

    def batch_process(self):


        start = time.time()
        post_one_time = None
        post_two_time = None

        times = []

        # This is the 'master' function, so it controls a lot

        # Run iterations
        for self._iteration in range(self.args.run.iterations):
            iteration_start = time.time()
            if self.args.mode.name == "train" and self._iteration >= self.args.run.iterations:

                logger.info('Finished training (iteration %d)' % self._iteration)
                self.checkpoint()
                break


            if self.args.mode.name == "train":
                self.val_step()
                self.train_step()
                self.checkpoint()
            else:
                self.ana_step()

            if post_one_time is None:
                post_one_time = time.time()
            elif post_two_time is None:
                post_two_time = time.time()
            times.append(time.time() - iteration_start)
        self.close_savers()

        end = time.time()

        logger.info(f"Total time to batch_process: {end - start}")
        if post_one_time is not None:
            logger.info(f"Total time to batch process except first iteration: {end - post_one_time}")
        if post_two_time is not None:
            logger.info(f"Total time to batch process except first two iterations: {end - post_two_time}")
        if len(times) > 40:
            logger.info(f"Total time to batch process last 40 iterations: {numpy.sum(times[-40:])}" )



    def build_lr_schedule(self, learning_rate_schedule = None):
        # Define the learning rate sequence:

        if learning_rate_schedule is None:
            learning_rate_schedule = {
                'warm_up' : {
                    'function'      : 'linear',
                    'start'         : 0,
                    'n_epochs'      : 1,
                    'initial_rate'  : 0.00001,
                },
                'flat' : {
                    'function'      : 'flat',
                    'start'         : 1,
                    'n_epochs'      : 20,
                },
                'decay' : {
                    'function'      : 'decay',
                    'start'         : 21,
                    'n_epochs'      : 4,
                    'floor'         : 0.00001,
                    'decay_rate'    : 0.999
                },
            }


        # one_cycle_schedule = {
        #     'ramp_up' : {
        #         'function'      : 'linear',
        #         'start'         : 0,
        #         'n_epochs'      : 10,
        #         'initial_rate'  : 0.00001,
        #         'final_rate'    : 0.001,
        #     },
        #     'ramp_down' : {
        #         'function'      : 'linear',
        #         'start'         : 10,
        #         'n_epochs'      : 10,
        #         'initial_rate'  : 0.001,
        #         'final_rate'    : 0.00001,
        #     },
        #     'decay' : {
        #         'function'      : 'decay',
        #         'start'         : 20,
        #         'n_epochs'      : 5,
        #         'rate'          : 0.00001
        #         'floor'         : 0.00001,
        #         'decay_rate'    : 0.99
        #     },
        # }
        # learning_rate_schedule = one_cycle_schedule

        # We build up the functions we need piecewise:
        func_list = []
        cond_list = []

        for i, key in enumerate(learning_rate_schedule):

            # First, create the condition for this stage
            start    = learning_rate_schedule[key]['start']
            length   = learning_rate_schedule[key]['n_epochs']

            if i +1 == len(learning_rate_schedule):
                # Make sure the condition is open ended if this is the last stage
                condition = lambda x, s=start, l=length: x >= s
            else:
                # otherwise bounded
                condition = lambda x, s=start, l=length: x >= s and x < s + l


            if learning_rate_schedule[key]['function'] == 'linear':

                initial_rate = learning_rate_schedule[key]['initial_rate']
                if 'final_rate' in learning_rate_schedule[key]: final_rate = learning_rate_schedule[key]['final_rate']
                else: final_rate = self.args.mode.optimizer.learning_rate

                function = lambda x, s=start, l=length, i=initial_rate, f=final_rate : numpy.interp(x, [s, s + l] ,[i, f] )

            elif learning_rate_schedule[key]['function'] == 'flat':
                if 'rate' in learning_rate_schedule[key]: rate = learning_rate_schedule[key]['rate']
                else: rate = self.args.mode.optimizer.learning_rate

                function = lambda x : rate

            elif learning_rate_schedule[key]['function'] == 'decay':
                decay    = learning_rate_schedule[key]['decay_rate']
                floor    = learning_rate_schedule[key]['floor']
                if 'rate' in learning_rate_schedule[key]: rate = learning_rate_schedule[key]['rate']
                else: rate = self.args.mode.optimizer.learning_rate

                function = lambda x, s=start, d=decay, f=floor: (rate-f) * numpy.exp( -(d * (x - s))) + f

            cond_list.append(condition)
            func_list.append(function)

        self.lr_calculator = lambda x: numpy.piecewise(
            x * (self.args.run.minibatch_size / self._train_data_size),
            [c(x * (self.args.run.minibatch_size / self._train_data_size)) for c in cond_list], func_list)
