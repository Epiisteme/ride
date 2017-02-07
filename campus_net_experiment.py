#! /usr/bin/python
NEW_SCRIPT_DESCRIPTION = '''Experiment that models failures in a campus network setting
and determines the effectiveness of several SDN/IP multicast tree-establishing algorithms
in improving data dissemination to subscribing IoT devices around the campus.
Intended as a simplified simulation that removes the need for network setup or
components such as switches, SDN controllers, etc.'''

# @author: Kyle Benson
# (c) Kyle Benson 2016

from failure_model import SmartCampusFailureModel
# TODO: from sdn_topology import *
from networkx_sdn_topology import NetworkxSdnTopology
import networkx as nx
import logging as log
import random
import json

import argparse
#from os.path import isdir
#from os import listdir
#from getpass import getpass
#password = getpass('Enter password: ')


class SmartCampusNetworkxExperiment(object):

    def __init__(self, nruns=1, ntrees=3, mcast_heuristic='closure', nsubscribers=5,
                 failure_model=None, topo=['networkx'],
                 debug='info', output_filename='results.json',
                 choice_rand_seed=None, rand_seed=None,
                 # NOTE: kwargs just used for construction via argparse
                 **kwargs):
        super(SmartCampusNetworkxExperiment, self).__init__()
        self.nruns = nruns
        self.ntrees = ntrees
        self.nsubscribers = nsubscribers
        self.output_filename = output_filename
        self.mcast_heuristic = mcast_heuristic

        log_level = log.getLevelName(debug.upper())
        log.basicConfig(format='%(levelname)s:%(message)s', level=log_level)

        self.random = random.Random(choice_rand_seed)
        random.seed(rand_seed)
        # QUESTION: do we need one for the algorithms as well?

        if failure_model is None:
            failure_model = SmartCampusFailureModel()
        self.failure_model = failure_model

        if topo[0] == 'networkx':
            topology = NetworkxSdnTopology(*topo[1:])
        else:
            raise NotImplementedError("unrecognized or unimplemented SdnTopology type %s" % topo[0])
        self.topo = topology

        # results are output as JSON to file after the experiment runs
        self.results = {'results': [], # each is a single run containing: {run: run#, heuristic_name: percent_reachable}
                        'params': {'ntrees': ntrees,
                                   'nsubscribers': nsubscribers,
                                   'failure_model': self.failure_model.get_params(),
                                   'topo': topo,
                                   # TODO: maybe use connectivity approximation to analyze how resilient the graphs are?
                                   'randseed': choice_rand_seed,
                                   }
                        }

        # HACK for saving failure model's rand seed
        try:
            self.results['params']['failrandseed'] = kwargs['failure_rand_seed']
        except IndexError:
            pass

    @classmethod
    def build_from_args(cls, args):
        """Constructs from an ArgumentParser object."""
        ##################################################################################
        #################      ARGUMENTS       ###########################################
        # ArgumentParser.add_argument(name or flags...[, action][, nargs][, const][, default][, type][, choices][, required][, help][, metavar][, dest])
        # action is one of: store[_const,_true,_false], append[_const], count
        # nargs is one of: N, ?(defaults to const when no args), *, +, argparse.REMAINDER
        # help supports %(var)s: help='default value is %(default)s'
        ##################################################################################

        parser = argparse.ArgumentParser(description=NEW_SCRIPT_DESCRIPTION,
                                         #formatter_class=argparse.RawTextHelpFormatter,
                                         #epilog='Text to display at the end of the help print',
                                         parents=[SmartCampusFailureModel.arg_parser]
                                         )

        # experimental treatment parameters
        parser.add_argument('--nruns', '-r', type=int, default=1,
                            help='''number of times to run experiment (default=%(default)s)''')
        parser.add_argument('--ntrees', '-t', type=int, default=3,
                            help='''number of redundant multicast trees to build (default=%(default)s)''')
        parser.add_argument('--mcast-heuristic', '-a', type=str, default='closure', dest='mcast_heuristic',
                            help='''heuristic algorithm for building multicast trees (default=%(default)s)''')
        # TODO: should this be a percentage? should also warn if too many subs based on model of how many would need mcast support
        parser.add_argument('--nsubscribers', '-s', type=int, default=5,
                            help='''number of multicast subscribers (terminals) to reach (default=%(default)s)''')
        parser.add_argument('--topo', type=str, default=['networkx'], nargs='+',
                            help='''type of SdnTopology to use and (optionally) its constructor parameters (default=%(default)s)''')

        # experiment interaction control
        parser.add_argument('--debug', '-d', type=str, default='info', nargs='?', const='debug',
                            help='''set debug level for logging facility (default=%(default)s, %(const)s when specified with no arg)''')
        parser.add_argument('--output-file', '-o', type=str, default='results.json', dest='output_filename',
                            help='''name of output file for recording JSON results (default=%(default)s)''')
        parser.add_argument('--choice-rand-seed', type=int, default=None, dest='choice_rand_seed',
                            help='''random seed for choices of subscribers & servers (default=%(default)s)''')
        parser.add_argument('--rand-seed', type=int, default=None, dest='rand_seed',
                            help='''random seed used by other classes via calls to random module (default=%(default)s)''')

        args = parser.parse_args(args)

        # convert to plain dict
        args = vars(args)
        failure_model = SmartCampusFailureModel(**args)
        args['failure_model'] = failure_model
        return cls(**args)

    def run_all_experiments(self):
        """Runs the requested experimental configuration
        for the requested number of times, saving the results to an output file."""
        for r in range(self.nruns):
            log.debug("Starting run %d" % r)
            # QUESTION: should we really do this each iteration?  won't it make for higher variance?
            subs = self.choose_subscribers()
            server = self.choose_server()
            failed_nodes, failed_links = self.apply_failure_model()
            # TODO: only need to do this each time for non-deterministic heuristics
            trees = self.build_mcast_trees(server, subs)
            result = self.run_experiment(failed_nodes, failed_links, server, subs, trees)
            result['run'] = r
            self.record_result(result)
        self.output_results()

    def record_result(self, result):
        """Result is a dict that includes the percentage of subscribers
        reachable as well as metadata such as run #"""
        self.results['results'].append(result)

    def output_results(self):
        """Outputs the results to a file"""
        log.info("Results: %s" % self.results)
        with open(self.output_filename, "w") as f:
            json.dump(self.results, f, sort_keys=True, indent=2)

    def apply_failure_model(self):
        """Removes edges/links from a copy of the topology according
        to the failure model.
        @:return modified networkx topology"""
        nodes, links = self.failure_model.apply_failure_model(self.topo)
        log.debug("Failed nodes: %s" % nodes)
        log.debug("Failed links: %s" % links)
        return nodes, links

    def choose_subscribers(self):
        hosts = self.topo.get_hosts()
        subs = self.random.sample(hosts, self.nsubscribers)
        log.debug("Subscribers: %s" % subs)
        return subs

    def choose_server(self):
        server = self.random.choice(self.topo.get_servers())
        log.debug("Server: %s" % server)
        return server

    def build_mcast_trees(self, source, subscribers):
        """Build redundant multicast trees over the specified subscribers using
        the requested heuristic algorithm."""

        trees = self.topo.get_redundant_multicast_trees(source, subscribers, self.ntrees, self.mcast_heuristic)

        # Need to record which heuristic and tree # we used for later
        for tree in trees:
            tree.graph['heuristic'] = self.mcast_heuristic

        # log.debug("MCast Trees: %s" % trees)

        return trees

    def run_experiment(self, failed_nodes, failed_links, server, subscribers, trees):
        """Check what percentage of subscribers are still reachable
        from the server after the failure model has been applied
        by removing the failed_nodes and failed_links from each tree
        as well as a copy of the overall topology (for the purpose of
        establishing an upper bound)."""


        # IDEA: for the whole topology and each mcast tree,
        # remove the failed nodes and links,
        # determine all reachable nodes in topology,
        # consider only those that are subscribers,
        # record % reachable by any/all topologies.
        # NOTE: the reachability from the whole topology gives us an
        # upper bound on how well the multicast algorithms could possibly do.

        subscribers = set(subscribers)
        result = {}

        # First, create a copy of whole topology as the 'oracle' heuristic,
        # which sees what subscribers are even reachable by ANY path.
        topo_copy = self.topo.topo.copy()
        topo_copy.graph['heuristic'] = 'oracle'
        topos_to_check = [topo_copy]
        res = self.get_reachability(failed_nodes, failed_links, server, subscribers, topos_to_check)
        result[topo_copy.graph['heuristic']] = res

        # Now check the redundant multicast trees
        res = self.get_reachability(failed_nodes, failed_links, server, subscribers, trees)
        heuristic = trees[0].graph['heuristic']  # we assume all trees from same heuristic
        result[heuristic] = res

        # TODO: probably want to output some other comparison metrics as well
        # i.e. best tree, worst tree, avg, hop counts, etc.?

        return result

    def get_reachability(self, failed_nodes, failed_links, server, subscribers, topologies):
        """Returns the average probability of reaching one of the subscribers from the
        server in any of the given topologies despite the given failures.  This function removes
        the failed nodes and links from the topology, so pass in a copy!"""

        # TODO: algorithm for choosing between trees?  probably need to refactor the loop below.
        # would it make use of some connectivity information to represent the picks being received at the server?

        all_subscribers_reachable = set()
        for topology in topologies:
            topology.remove_edges_from(failed_links)
            topology.remove_nodes_from(failed_nodes)

            nodes_reachable = set(nx.single_source_shortest_path(topology, server))
            # could also use has_path()
            subscribers_reachable = subscribers.intersection(nodes_reachable)
            all_subscribers_reachable.update(subscribers_reachable)

            log.debug("%s heuristic reached %d subscribers" % (topology.graph['heuristic'], len(subscribers_reachable)))
        return float(len(all_subscribers_reachable)) / len(subscribers)


if __name__ == "__main__":
    import sys
    exp = SmartCampusNetworkxExperiment.build_from_args(sys.argv[1:])
    exp.run_all_experiments()

