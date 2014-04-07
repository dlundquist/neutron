import abc

import six


@six.add_metaclass(abc.ABCMeta)
class HaproxyBackend(object):
    """Abstract backend that defines the interface for an haproxy backend."""

    @abc.abstractmethod
    def create(self, logical_config):
        """Sets up and starts haproxy and required services on the backend."""
        pass

    @abc.abstractmethod
    def update(self, logical_config):
        """Updates haproxy and required services on the backend."""
        pass

    @abc.abstractmethod
    def delete(self, logical_config):
        """Stops haproxy and removes unwanted data on the backend."""
        pass

    @abc.abstractmethod
    def exists(self, logical_config):
        """Checks if an haproxy exists on the backend."""
        pass

    @abc.abstractmethod
    def get_config(self, logical_config):
        pass

    @abc.abstractmethod
    def get_stats(self, logical_config):
        """Returns haproxy statistics."""
        pass
