"""
Module containing classes and methods for Model functionality in Propnet code.
"""

import numpy as np
import os
from abc import ABC, abstractmethod
from itertools import chain

from monty.serialization import loadfn
from monty.json import MSONable

import sympy as sp
from sympy.parsing.sympy_parser import parse_expr

from propnet.symbols import DEFAULT_SYMBOLS
from propnet import ureg
from propnet.core.exceptions import ModelEvaluationError

# General TODOs:
# TODO: Constraints are really just models that output True/False
#       can we refactor with this?
# TODO: I'm not sure that symbol_map needs to be present in all models,
#       maybe just equation models/relegated to that plug_in method
# TODO: The evaluate/plug_in dichotomy is a big confusing here
#       I suspect they can be consolidated
# TODO: Does the unit_map really need to be specified?  Why can't
#       pint handle this?
class Model(ABC):
    """
    Abstract model class for all models appearing in Propnet

    Args:
        name (str): title of the model
        connections (dict): list of connections dictionaries,
            which take the form {"inputs": [Symbols], "outputs": [Symbols]},
            for example:
            connections = [{"inputs": ["p", "T"], "outputs": ["V"]},
                           {"inputs": ["T", "V"], "outputs": ["p"]}]
        constraints (str): title
        description (str): long form description of the model
        categories (str): list of categories applicable to
            the model
        references ([str]): list of the informational links
            explaining / supporting the model

    """
    def __init__(self, name, connections, constraints=None,
                 description=None, categories=None, references=None,
                 symbol_map=None, unit_map=None):
        self.name = name
        self.connections = connections
        self.description = description
        self.categories = categories
        self.references = references
        self.constraints = constraints
        # If no symbol map specified, use inputs/outputs
        self.symbol_map = symbol_map
        self.unit_map = unit_map or {}
        # This basically dictates that the unit map should be
        # consistent with the plug-in or model symbols, hopefully
        # to be removed when unitization is refactored on the symbol side
        if self.symbol_map and self.unit_map:
            self.unit_map = {self.symbol_map.get(symbol) or symbol: value
                             for symbol, value in self.unit_map.items()}


    @abstractmethod
    def plug_in(self, symbol_value_dict):
        """
        Plugs in a symbol to quantity dictionary

        Args:
            symbol_value_dict ({symbol: value}): a mapping
                of symbols to values to be substituted
                into the model to yield output

        Returns:
            dictionary of output symbols with associated
                values generated from the input
        """
        return

    # TODO: I'm really not crazy about the "successful" key implementation
    #       preventing model failure using try/except is the path to
    #       the dark side
    def evaluate(self, symbol_value_dict):
        """
        Given a set of symbol_values, performs error checking to see if
        the input symbol_values represents a valid input set based on
        the self.connections() method. If so, it returns a dictionary
        representing the value of plug_in applied to the inputs. The
        dictionary contains a "successful" key representing if plug_in
        was successful.

        Args:
            symbol_value_dict ({symbol: value}): a mapping of symbols
                to values to be substituted into the model

        Returns:
            dictionary of output symbols with associated values
            generated from the input, along "successful" if the
            substitution succeeds
        """
        # Remap symbols and units if symbol map isn't none
        if self.symbol_map:
            symbol_value_dict = {self.symbol_map[symbol]: value
                                 for symbol, value in symbol_value_dict.items()}

        # TODO: Is it really necessary to strip these?
        # TODO: maybe this only applies to pymodels or things with objects?
        # strip units from input
        for symbol, value in symbol_value_dict.items():
            if isinstance(value, ureg.Quantity):
                if symbol in self.unit_map:
                    value = value.to(self.unit_map[symbol])
                symbol_value_dict[symbol] = value.magnitude

        available_symbols = set(symbol_value_dict.keys())

        # check we support this combination of inputs
        input_matches = [set(input_set) == available_symbols
                         for input_set in self.input_sets]
        if not any(input_matches):
            return {
                'successful': False,
                'message': "The {} model cannot generate any outputs for these inputs: {}".format(
                    self.name, available_symbols)
            }
        try:
            # evaluate is allowed to fail
            out = self.plug_in(symbol_value_dict)
            out['successful'] = True
        except Exception as e:
            return {
                'successful': False,
                'message': str(e)
            }

        # add units to output
        for key in out:
            if key == 'successful':
                continue
            out[key] = ureg.Quantity(out[key], self.unit_map[key])
        return out

    # TODO: these could be more descriptively named, maybe input_sets
    #       vs. all_inputs
    @property
    def input_sets(self):
        return [set(d['inputs']) for d in self.connections]

    @property
    def output_sets(self):
        return [set(d['outputs']) for d in self.connections]

    @property
    def all_inputs(self):
        return list(chain.from_iterable(self.inputs))

    @property
    def all_outputs(self):
        return list(chain.from_iterable(self.outputs))

    @property
    def all_symbols(self):
        return self.all_inputs + self.all_outputs

    def test(self, inputs, outputs):
        """
        Runs a test of the model to determine whether its operation
        is consistent with the specified inputs and outputs

        Args:
            inputs (dict): set of input names to values
            outputs (dict): set of output names to values
        """
        model_outputs = self.evaluate(inputs)
        for k, known_output in outputs.items():
            if not model_outputs == known_output:
                raise ModelEvaluationError(
                    "Model output does not match known output for {}".format(
                        self.name))
        return True

    def validate_from_test_data(self):
        """
        Validates from test data based on the model name

        Returns:
            True if validation completes successfully
        """


class EquationModel(Model, MSONable):
    """
    Equation model is a Model subclass which is invoked
    from a list of equations

    Args:
        name (str): title of the model
        connections (dict): list of connections dictionaries,
            which take the form {"inputs": [Symbols], "outputs": [Symbols]},
            for example:
            connections = [{"inputs": ["p", "T"], "outputs": ["V"]},
                           {"inputs": ["T", "V"], "outputs": ["p"]}]
        constraints (str): title
        description (str): long form description of the model
        categories (str): list of categories applicable to
            the model
        references ([str]): list of the informational links
            explaining / supporting the model

    """
    def __init__(self, name, equations, connections, symbol_map=None,
                 constraints=None, description=None, categories=None,
                 references=None, unit_map=None):
        self.equations = equations
        super(EquationModel, self).__init__(
            name, connections, constraints, description,
            categories, references, symbol_map, unit_map)

    # TODO: shouldn't this respect/use connections info,
    #       or is that done elsewhere?
    def plug_in(self, symbol_value_dict):
        # Parse equations and substitute
        eqns = [parse_expr(eq) for eq in self.equations]
        eqns = [eqn.subs(symbol_value_dict) for eqn in eqns]
        possible_outputs = set()
        for eqn in eqns:
            possible_outputs = possible_outputs.union(eqn.free_symbols)
        outputs = {}
        # Determine outputs from solutions to substituted equations
        for possible_output in possible_outputs:
            solutions = sp.nonlinsolve(eqns, possible_output)
            # taking first solution only, and only asking for one output symbol
            # so know length of output tuple for solutions will be 1
            solution = list(solutions)[0][0]
            if not isinstance(solution, sp.EmptySet):
                outputs[str(possible_output)] = float(sp.N(solution))
        return outputs

    @classmethod
    def from_file(cls, filename):
        """Load model from file"""
        model = loadfn(filename)
        if isinstance(model, Model):
            return model
        else:
            return cls.from_dict(model)

    @classmethod
    def from_preset(cls, name):
        """Loads from preset library of models"""
        loc = os.path.join("..", "models", "{}.yaml".format(name))
        if os.path.isfile(loc):
            return cls.from_file(loc)


class PyModel(Model):
    """
    Purely python based model which allows for a flexible "plug_in"
    method as input, then invokes that method in the defined plug-in
    method
    """
    def __init__(self, name, connections, plug_in, constraints=None,
                 description=None, categories=None, references=None,
                 symbol_map=None):
        self._plug_in = plug_in
        super(PyModel, self).__init__(
            name, connections, constraints, description,
            categories, references, symbol_map)

    def plug_in(self, symbol_value_dict):
        return self._plug_in(symbol_value_dict)


# Note that this class exists purely as a factory method for PyModel
# which could be implemented as a class method of PyModel
# but wouldn't serialize as cleanly
class PyModuleModel(PyModel):
    def __init__(self, module_path):
        self._module_path = module_path
        mod = __import__(module_path, globals(), locals(), ['config'], 0)
        super(PyModuleModel, self).__init__(**mod.config)

    def as_dict(self):
        return {"module_path": self._module_path,
                "@module": "propnet.core.model",
                "@class": "PyModuleModel"}