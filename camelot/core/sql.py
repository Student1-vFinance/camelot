#  ============================================================================
#
#  Copyright (C) 2007-2011 Conceptive Engineering bvba. All rights reserved.
#  www.conceptive.be / project-camelot@conceptive.be
#
#  This file is part of the Camelot Library.
#
#  This file may be used under the terms of the GNU General Public
#  License version 2.0 as published by the Free Software Foundation
#  and appearing in the file license.txt included in the packaging of
#  this file.  Please review this information to ensure GNU
#  General Public Licensing requirements will be met.
#
#  If you are unsure which license is appropriate for your use, please
#  visit www.python-camelot.com or contact project-camelot@conceptive.be
#
#  This file is provided AS IS with NO WARRANTY OF ANY KIND, INCLUDING THE
#  WARRANTY OF DESIGN, MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
#
#  For use of this library in commercial applications, please contact
#  project-camelot@conceptive.be
#
#  ============================================================================
import logging
from functools import wraps

import sqlalchemy.sql.operators

from camelot.core.conf import settings
from camelot.core.exception import UserException

LOGGER = logging.getLogger('camelot.core.sql')

def like_op(column, string):
    return sqlalchemy.sql.operators.like_op(column, '%%%s%%'%string)

def transaction(original_function):
    """Decorator for methods on an entity, to make them transactional"""

    logger = logging.getLogger('camelot.core.sql.transaction')
    
    @wraps( original_function )
    def decorated_function(cls, *args, **kwargs):
        session = cls.query.session
        session.begin()
        try:
            result = original_function(cls, *args, **kwargs)
            session.commit()
        except Exception, e:
            if not isinstance( e, (UserException,) ):
                logger.error('Unhandled exception, rolling back transaction', 
                             exc_info=e)
            session.rollback()
            raise e
        return result
    
    return decorated_function

def update_database_from_model():
    """Introspection the model and add missing columns in the database
    
    this function can be ran in setup_model after setup_all(create_tables=True)
    """
    migrate_engine = settings.ENGINE()
    migrate_connection = migrate_engine.connect()
    
    from sqlalchemy.schema import MetaData 
    from camelot.model import metadata
    from migrate.versioning import schemadiff
    from migrate.changeset import create_column
    schema_diff = schemadiff.SchemaDiff(metadata, MetaData(migrate_connection, reflect=True))
   
    for table, difference in schema_diff.tables_different:
        for column in difference.columns_missing_from_B:
            LOGGER.warn( 'column %s missing in table %s'%(column, table) )
            create_column(column, table)
