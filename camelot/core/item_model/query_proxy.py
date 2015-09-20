import functools
import logging

import six

from sqlalchemy import orm, sql, exc

from ..qt import Qt
from .list_proxy import ListModelProxy

LOGGER = logging.getLogger(__name__)

class QueryModelProxy(ListModelProxy):
    """
    A concrete model proxy for displaying objects in sqlalchemy query objects
    """

    def __init__(self, query):
        """
        :param query: a sqlalchemy query
        """
        super(QueryModelProxy, self).__init__([])
        self._query = query
        self._length = None
        self._filters = dict()
        self._sort_decorator = self._get_sort_decorator()

    def __len__(self):
        if self._length is None:
            # manipulate the query to circumvent the use of subselects and order by
            # clauses
            query = self.get_query(order_clause=False)
            mapper = query._mapper_zero()
            select = query.order_by(None).as_scalar()
            columns = [sql.func.count(mapper.primary_key[0])]
            select = select.with_only_columns(columns)
            self._length = query.session.execute(select, mapper=mapper).scalar()
        return self._length + len(self._objects)

    def get_query(self, order_clause=True):
        """
        :return: the query used to fetch the data, this is not the same as the
            one set in the constructor, as sorting and filters will modify it.
        """
        query = self._query
        # filters might be changed in the gui thread while being iterated
        for filter_, value in six.iteritems(self._filters.copy()):
            query = filter_.decorate_query(query, value)
        if order_clause:
            query = self._sort_decorator(query)
        return query

    def _extend_indexed_objects(self, offset, limit):
        LOGGER.debug('extend cache from {0} with limit {1}'.format(offset, limit))
        if limit > 0:
            query = self.get_query().offset(offset).limit(limit)
            free_index = offset
            for obj in query.all():
                # find a free index for the object
                while self._indexed_objects.get(free_index) not in (None, obj):
                    free_index += 1
                # check if the object is not present with another index
                if self._indexed_objects.get(obj) in (None, free_index):
                    self._indexed_objects[free_index] = obj
            row_count = len(self)
            rows_in_query = row_count - len(self._objects)
            # Verify if rows not in the query have been requested
            if offset+limit >= rows_in_query:
                for row in range(max(rows_in_query, offset), min(offset+limit, row_count)):
                    obj = self._objects[row - rows_in_query]
                    self._indexed_objects[row] = obj

    def _get_sort_decorator( self, column=None, order=None ):
        """set the sort decorator attribute of this model to a function that
        sorts a query by the given column using the given order.  When no
        arguments are given, use the default sorting, which is according to
        the primary keys of the model.  This to impose a strict ordening of
        the rows in the model.
        """

        order_by, join = [], None

        mapper = self._query._mapper_zero()
        #
        # First sort according the requested column
        #
        if None not in (column, order):
            property = None
            field_name = self._columns[column][0]
            class_attribute = getattr(self.admin.entity, field_name)

            #
            # The class attribute of a hybrid property can be an sql clause
            #

            if isinstance(class_attribute, sql.ClauseElement):
                order_by.append((class_attribute, order))

            try:
                property = mapper.get_property(
                    field_name,
                )
            except exc.InvalidRequestError:
                pass
            
            # If the field is a relation: 
            #  If it specifies an order_by option we have to join the related table, 
            #  else we use the foreign key as sort field, without joining
            if property and isinstance(property, orm.properties.RelationshipProperty):
                target = property.mapper
                if target:
                    if target.order_by:
                        join = field_name
                        class_attribute = target.order_by[0]
                    else:
                        class_attribute = list(property._calculated_foreign_keys)[0]
            if property:
                order_by.append((class_attribute, order))
                                
        def sort_decorator(order_by, join, query):
            order_by = list(order_by)
            if join:
                query = query.outerjoin(join)
            # remove existing order clauses, because they might interfer
            # with the requested order from the user, as the existing order
            # clause is first in the list, and put them at the end of the list
            if query._order_by:
                for order_by_column in query._order_by:
                    order_by.append((order_by_column, Qt.AscendingOrder))
            #
            # Next sort according to default sort column if any
            #
            if mapper.order_by:
                for mapper_order_by in mapper.order_by:
                    order_by.append((mapper_order_by, Qt.AscendingOrder))
            #
            # In the end, sort according to the primary keys of the model, to enforce
            # a unique order in any case
            #
            for primary_key_column in mapper.primary_key:
                order_by.append((primary_key_column, Qt.AscendingOrder))
            query = query.order_by(None)
            order_by_columns = set()
            for order_by_column, order in order_by:
                if order_by_column not in order_by_columns:
                    if order == Qt.AscendingOrder:
                        query = query.order_by(order_by_column)
                    else:
                        query = query.order_by(sql.desc(order_by_column))
                    order_by_columns.add(order_by_column)
            return query
        
        return functools.partial(sort_decorator,
                                 order_by,
                                 join)


