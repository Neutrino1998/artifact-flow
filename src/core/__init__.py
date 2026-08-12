"""Core modules are imported explicitly from their defining submodules.

Keeping this package initializer side-effect free is part of the embeddable
runtime boundary: importing ``core.task_supervisor`` must not eagerly import
the Conversation turn handler, database models, or Web application stack.
"""
