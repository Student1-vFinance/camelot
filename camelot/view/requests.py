from dataclasses import dataclass
import logging
import typing

from ..core.exception import CancelRequest
from ..core.naming import (
    CompositeName, NameNotFoundException, initial_naming_context
)
from ..core.serializable import (
    DataclassSerializable, NamedDataclassSerializable, Serializable
)
from ..admin.action import ActionStep

LOGGER = logging.getLogger('camelot.view.requests')

class ModelRun(object):
    """
    Server side information of an ongoing action run
    """

    def __init__(self, gui_run_name: CompositeName, generator):
        self.gui_run_name = gui_run_name
        self.generator = generator
        self.cancel = False
        self.last_step = None

model_run_names = initial_naming_context.bind_new_context('model_run')

class AbstractRequest(NamedDataclassSerializable):
    """
    Serialiazable Requests the UI can send to the model
    """

    @classmethod
    def execute(cls, request_data, response_handler, cancel_handler):
        raise NotImplementedError

    @classmethod
    def _iterate_until_blocking(cls, run_name, method, args, response_handler, cancel_handler):
        """Helper calling for generator methods.  The decorated method iterates
        the generator until the generator yields an :class:`ActionStep` object that
        is blocking.  If a non blocking :class:`ActionStep` object is yielded, then
        send it to the GUI thread for execution through the signal slot mechanism.
        
        :param generator_method: the method of the generator to be called
        :param *args: the arguments to use when calling the generator method.
        """
        from camelot.view.action_steps import MessageBox, PopProgressLevel
        try:
            run = initial_naming_context.resolve(run_name)
        except NameNotFoundException:
            LOGGER.error('Run name not found : {}'.format(run_name))
            return
        gui_run_name = run.gui_run_name
        try:
            if method == 'continue':
                result = None
            elif method == 'send':
                response = run.last_step.deserialize_result(None, args)
                result = run.generator.send(response)
            elif method == 'throw':
                result = run.generator.throw(args)
            else:
                raise Exception('Unhandled method')
            while True:
                if isinstance(result, ActionStep):
                    run.last_step = result
                    if isinstance(result, (DataclassSerializable,)):
                        LOGGER.debug('serializable step, use signal slot')
                        response_handler.serializable_action_step_signal.emit(
                            run_name, gui_run_name, type(result).__name__,
                            result.blocking, result._to_bytes()
                        )
                        if result.blocking:
                            # this step is blocking, interrupt the loop
                            return
                    elif result.blocking:
                        LOGGER.debug( 'non serializable blocking step : {}'.format(result) )
                        raise Exception('This should not happen')
                    else:
                        LOGGER.debug( 'non blocking step, use signal slot' )
                        response_handler.non_blocking_action_step_signal.emit(
                            run_name, gui_run_name, result
                        )
                #
                # Cancel requests can arrive asynchronously through non 
                # blocking ActionSteps such as UpdateProgress
                #
                if cancel_handler.has_cancel_request():
                    LOGGER.debug( 'asynchronous cancel, raise request' )
                    result = run.generator.throw(CancelRequest())
                else:
                    LOGGER.debug( 'move iterator forward' )
                    result = next(run.generator)
        except CancelRequest as e:
            LOGGER.debug( 'iterator raised cancel request, pass it' )
            response_handler.serializable_action_step_signal.emit(
                run_name, gui_run_name, PopProgressLevel.__name__, False,
                PopProgressLevel()._to_bytes()
            )
            response_handler.action_stopped_signal.emit(run_name, gui_run_name, e)
        except StopIteration as e:
            LOGGER.debug( 'iterator raised stop, pass it' )
            response_handler.serializable_action_step_signal.emit(
                run_name, gui_run_name, PopProgressLevel.__name__, False,
                PopProgressLevel()._to_bytes()
            )
            initial_naming_context.unbind(run_name)
            response_handler.action_stopped_signal.emit(run_name, gui_run_name, e)
        except Exception as e:
            response_handler.serializable_action_step_signal.emit(
                ('constant', 'null'), gui_run_name, MessageBox.__name__, False,
                MessageBox.from_exception(
                    LOGGER,
                    'Exception caught',
                    e
                )._to_bytes()
            )
            LOGGER.debug( 'iterator raised stop, pass it' )
            response_handler.serializable_action_step_signal.emit(
                run_name, gui_run_name, PopProgressLevel.__name__, False,
                PopProgressLevel()._to_bytes()
            )
            initial_naming_context.unbind(run_name)
            response_handler.action_stopped_signal.emit(run_name, gui_run_name, e)

@dataclass
class InitiateAction(AbstractRequest):
    """
    Initiate a new run of an action, the run is uniquely identified on the
    client side by its gui_run_name
    """
    gui_run_name: CompositeName
    action_name: CompositeName
    model_context: CompositeName
    mode: typing.Union[str, dict, list, int]

    @classmethod
    def execute(cls, request_data, response_handler, cancel_handler):
        from .action_steps import PushProgressLevel, MessageBox
        LOGGER.debug('Iniate run of action {}'.format(request_data['action_name']))
        action = initial_naming_context.resolve(tuple(request_data['action_name']))
        gui_run_name = tuple(request_data['gui_run_name'])
        try:
            model_context = initial_naming_context.resolve(tuple(request_data['model_context']))
        except NameNotFoundException:
            LOGGER.error('Could not create model context, no binding for name: {}'.format(request_data['model_context']))
            response_handler.action_stopped_signal.emit(('constant', 'null'), gui_run_name, None)
            return
        try:
            generator = action.model_run(model_context, request_data.get('mode'))
        except Exception as exception:
            response_handler.serializable_action_step_signal.emit(
                ('constant', 'null'), gui_run_name, MessageBox.__name__, False,
                MessageBox.from_exception(
                    LOGGER,
                    'Exception caught in {}'.format(type(action).__name__),
                    exception
                )._to_bytes()
            )
            response_handler.action_stopped_signal.emit(('constant', 'null'), gui_run_name, None)
        if generator is None:
            response_handler.action_stopped_signal.emit(('constant', 'null'), gui_run_name, None)
        run = ModelRun(gui_run_name, generator)
        run_name = model_run_names.bind(str(id(run)), run)
        response_handler.serializable_action_step_signal.emit(
            run_name, gui_run_name, PushProgressLevel.__name__, False,
            PushProgressLevel('Please wait')._to_bytes()
        )
        LOGGER.debug('Action {} runs in generator {}'.format(request_data['action_name'], run_name))
        cls._iterate_until_blocking(
            run_name, 'continue', None, response_handler, cancel_handler
        )

@dataclass
class SendActionResponse(AbstractRequest):
    """
    Send a response to a running action that is waiting for the response from
    the client.  The running action is uniquely identied on the server side
    by its run_name.
    """
    run_name: CompositeName
    response: Serializable

    @classmethod
    def execute(cls, request_data, response_handler, cancel_handler):
        cls._iterate_until_blocking(
            tuple(request_data['run_name']), 'send', request_data['response'],
            response_handler, cancel_handler
        )

@dataclass
class ThrowActionException(AbstractRequest):
    """
    Raise an exception within an action that is waiting for the response
    from the client.  The running action is uniquely identied on the server side
    by its run_name.
    """
    run_name: CompositeName
    exception: Serializable

    @classmethod
    def execute(cls, request_data, response_handler, cancel_handler):
        cls._iterate_until_blocking(
            tuple(request_data['run_name']), 'throw', request_data['exception'],
            response_handler, cancel_handler
        )

@dataclass
class CancelAction(AbstractRequest):
    """
    Request an action run to be canceled, even if the action is not waiting
    for a response. The running action is uniquely identied on the server side
    by its run_name.
    """
    run_name: CompositeName

    @classmethod
    def execute(cls, request_data, response_handler, cancel_handler):
        pass