import abc
import six


@six.add_metaclass(abc.ABCMeta)
class AbstractPolicyAction(object):
    @abc.abstractmethod
    def get_type(self):
        """Return one of predefined type of the hosting vnf drivers."""
        pass

    @abc.abstractmethod
    def get_name(self):
        """Return a symbolic name for the service VM plugin."""
        pass

    @abc.abstractmethod
    def get_description(self):
        pass

    @abc.abstractmethod
    def execute_action(self, plugin, context, vnf_dict, args):
        """args: policy is enabled to execute with additional arguments."""
        pass
