#  ============================================================================
#
#  Copyright (C) 2007-2016 Conceptive Engineering bvba.
#  www.conceptive.be / info@conceptive.be
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#      * Redistributions of source code must retain the above copyright
#        notice, this list of conditions and the following disclaimer.
#      * Redistributions in binary form must reproduce the above copyright
#        notice, this list of conditions and the following disclaimer in the
#        documentation and/or other materials provided with the distribution.
#      * Neither the name of Conceptive Engineering nor the
#        names of its contributors may be used to endorse or promote products
#        derived from this software without specific prior written permission.
#  
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
#  ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
#  WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#  DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
#  DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
#  (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
#  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
#  ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
#  SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
#  ============================================================================

"""
Various ``ActionStep`` subclasses that manipulate the `item_view` of 
the `ListActionGuiContext`.
"""

from dataclasses import dataclass, InitVar, field
from typing import Union, List, Tuple
import json
import logging

from ...admin.admin_route import Route
from ...admin.action.base import ActionStep, RenderHint, State
from ...admin.action.list_action import ListActionModelContext, ListActionGuiContext, ApplicationActionGuiContext
from ...admin.action.list_filter import SearchFilter, Filter, All
from ...core.item_model import ProxyRegistry
from ...core.naming import initial_naming_context
from ...core.qt import Qt
from ...core.serializable import DataclassSerializable
from ...core.utils import ugettext_lazy
from ..workspace import show_top_level
from ..proxy.collection_proxy import (
    CollectionProxy, RowCount, RowData, SetColumns
)
from ..qml_view import qml_action_step

LOGGER = logging.getLogger(__name__)

@dataclass
class Sort( ActionStep, DataclassSerializable ):
    """Sort the items in the item view ( list, table or tree )

            :param column: the index of the column on which to sort
            :param order: a :class:`Qt.SortOrder`
    """
    column: int
    order: Qt.SortOrder = Qt.SortOrder.AscendingOrder

    @classmethod
    def gui_run(cls, gui_context, serialized_step):
        step = json.loads(serialized_step)
        model = gui_context.get_item_model()
        if model is not None:
            model.sort( step["column"], step["order"] )

row_count_instance = RowCount()
set_columns_instance = SetColumns()
row_data_instance = RowData()

@dataclass
class CrudActions(DataclassSerializable):
    """
    A data class which contains the routes to crud actions available
    to the gui to invoke.
    """

    admin: InitVar
    row_count: Route = field(init=False)
    set_columns: Route = field(init=False)
    row_data: Route = field(init=False)

    def __post_init__(self, admin):
        self.row_count = admin._register_action_route(
            admin.get_admin_route(), row_count_instance
        )
        self.row_data = admin._register_action_route(
            admin.get_admin_route(), row_data_instance
        )
        self.set_columns = admin._register_action_route(
            admin.get_admin_route(), set_columns_instance
        )

@dataclass
class UpdateTableView( ActionStep, DataclassSerializable ):
    """Change the admin and or value of an existing table view
    
    :param admin: an `camelot.admin.object_admin.ObjectAdmin` instance
    :param value: a list of objects or a query
    
    """

    admin: InitVar
    value: InitVar
    search_text: InitVar[Union[str, None]] = None
    title: Union[str, ugettext_lazy] = field(init=False)
    columns: List[str] = field(init=False)
    list_action: Union[Route, None] = field(init=False)
    proxy_route: Route = field(init=False)
    actions: List[Tuple[Route, RenderHint]] = field(init=False)
    action_states: List[Tuple[Route, State]] = field(default_factory=list)
    crud_actions: CrudActions = field(init=False)

    def __post_init__(self, admin, value, search_text):
        self.value = value
        self.title = admin.get_verbose_name_plural()
        self._post_init_actions__(admin)
        self.columns = admin.get_columns()
        self.list_action = admin.get_list_action()
        proxy = admin.get_proxy(value)
        if search_text is not None:
            for action_route in self.actions:
                action = initial_naming_context.resolve(action_route.route)
                if isinstance(action, SearchFilter):
                    search_strategies = list(admin._get_search_fields(search_text))
                    search_value = (search_text, *search_strategies)
                    proxy.filter(action, search_value)
                    break
            else:
                LOGGER.warn('No SearchFilter found to apply search text')

        self.proxy_route = ProxyRegistry.register(proxy)
        self._add_action_states(admin, proxy, self.actions, self.action_states)
        self.set_filters(self.action_states, proxy)
        self.crud_actions = CrudActions(admin)

    def _post_init_actions__(self, admin):
        self.actions = admin.get_list_actions().copy()
        self.actions.extend(admin.get_filters())
        self.actions.extend(admin.get_list_toolbar_actions())

    @staticmethod
    def _add_action_states(admin, proxy, actions, action_states):
        model_context = ListActionModelContext()
        model_context.admin = admin
        model_context.proxy = proxy
        for action_route in actions:
            action = initial_naming_context.resolve(action_route.route)
            state = action.get_state(model_context)
            action_states.append((action_route.route, state))

    @staticmethod
    def set_filters(action_states, model):
        for action_state in action_states:
            route = tuple(action_state[0])
            action = initial_naming_context.resolve(route)
            if not isinstance(action, Filter):
                continue
            state = action_state[1]
            values = [mode.value for mode in state.modes if mode.checked]
            # if all modes are checked, replace with [All]
            if len(values) == len(state.modes):
                values = [All]
            model.filter(action, values)

    @classmethod
    def gui_run(cls, gui_context, serialized_step):
        step = json.loads(serialized_step)
        cls.update_table_view(gui_context.view, step)
        gui_context.view.change_title(step['title'])

        gui_context.view.findChild(Qt)


@dataclass
class OpenTableView( UpdateTableView ):
    """Open a new table view in the workspace.
    
    :param admin: an `camelot.admin.object_admin.ObjectAdmin` instance
    :param value: a list of objects or a query

    .. attribute:: title
        the title of the the new view
        
    .. attribute:: subclasses
        a tree of subclasses to be displayed on the left of the

    .. attribute:: new_tab
        open the view in a new tab instead of the current tab
        
    """
    new_tab: bool = False
    admin_route: Route = field(init=False)

    def __post_init__(self, admin, value, search_text):
        super(OpenTableView, self).__post_init__(admin, value, search_text)
        self.admin_route = admin.get_admin_route()

    @classmethod
    def render(cls, gui_context, step):
        from camelot.view.controls.tableview import TableView
        table_view = TableView(gui_context, tuple(step['admin_route']))
        cls.update_table_view(table_view, step)
        return table_view
        
    @classmethod
    def gui_run(cls, gui_context, serialized_step):
        step = json.loads(serialized_step)
        table_view = cls.render(gui_context, step)
        if gui_context.workspace is not None:
            if step['new_tab'] == True:
                gui_context.workspace.add_view(table_view)
            else:
                gui_context.workspace.set_view(table_view)
        else:
            table_view.setObjectName('table.{}.{}'.format(
                step['admin_name'], id(table_view)
            ))
            show_top_level(table_view, None)
        table_view.change_title(step['title'])
        table_view.setFocus(Qt.FocusReason.PopupFocusReason)



@dataclass
class OpenQmlTableView(OpenTableView):
    """Open a new table view in the workspace.
    
    :param admin: an `camelot.admin.object_admin.ObjectAdmin` instance
    :param value: a list of objects or a query

    .. attribute:: title
        the title of the the new view

    .. attribute:: new_tab
        open the view in a new tab instead of the current tab
        
    """

    def __init__(self, admin, value, search_text=None):
        super().__init__(admin, value, search_text=search_text)
        self.list_action = admin.get_list_action()

    @classmethod
    def render(cls, gui_context, action_step_name, serialized_step):
        step = json.loads(serialized_step)

        class QmlListActionGuiContext(ListActionGuiContext):

            def get_progress_dialog(self):
                return ApplicationActionGuiContext.get_progress_dialog(self)

        list_gui_context = gui_context.copy(QmlListActionGuiContext)
        list_gui_context.admin_route = tuple(step['admin_route'])
        list_gui_context.context_id = None

        new_model = CollectionProxy(tuple(step['admin_route']))
        list(new_model.add_columns(step['columns']))
        new_model.set_value(step['proxy_route'])

        for action in step['actions']:
            render_hint = action['render_hint']
            if render_hint in ['combo_box', 'non_exclusive_group_box', 'exclusive_group_box']:
                continue
            new_model.add_action_route(tuple(action['route']))

        response = qml_action_step(list_gui_context, action_step_name,
                serialized_step, { 'model': new_model }, model=new_model)

        return response, new_model

    @classmethod
    def gui_run(cls, gui_context, serialized_step):
        cls.render(gui_context, 'OpenTableView', serialized_step)

@dataclass
class ToFirstRow(ActionStep, DataclassSerializable):
    """Move to the first row in a table"""

    @classmethod
    def gui_run(cls, gui_context, serialized_step):
        if gui_context.item_view is not None:
            gui_context.item_view.selectRow( 0 )
        else:
            qml_action_step(gui_context, 'ToFirstRow')

@dataclass
class ToLastRow(ActionStep, DataclassSerializable):
    """Move to the last row in a table"""

    @classmethod
    def gui_run(cls, gui_context, serialized_step):
        if gui_context.item_view is not None:
            item_view = gui_context.item_view
            item_view.selectRow( item_view.model().rowCount() - 1 )
        else:
            qml_action_step(gui_context, 'ToLastRow')

@dataclass
class ClearSelection(ActionStep, DataclassSerializable):
    """Deselect all selected items."""

    @classmethod
    def gui_run(cls, gui_context, serialized_step):
        if gui_context.item_view is not None:
            gui_context.item_view.clearSelection()
        else:
            qml_action_step(gui_context, 'ClearSelection', serialized_step)

@dataclass
class SetSelection(ActionStep, DataclassSerializable):
    """Set selection."""

    rows: List[int] = field(default_factory=list)

    @classmethod
    def gui_run(cls, gui_context, serialized_step):
        qml_action_step(gui_context, 'SetSelection', serialized_step)

@dataclass
class RefreshItemView(ActionStep, DataclassSerializable):
    """
    Refresh only the current item view
    """

    @classmethod
    def gui_run(cls, gui_context, serialized_step):
        model = gui_context.get_item_model()
        if model is not None:
            model.refresh()
