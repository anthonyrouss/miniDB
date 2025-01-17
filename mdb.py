import os
import re
from pprint import pprint
import sys
import readline
import traceback
import shutil

sys.path.append('miniDB')

from database import Database
from table import Table
from misc import convert_to_RA
# art font is "big"
art = '''
             _         _  _____   ____
            (_)       (_)|  __ \ |  _ \ 
  _ __ ___   _  _ __   _ | |  | || |_) |
 | '_ ` _ \ | || '_ \ | || |  | ||  _ <
 | | | | | || || | | || || |__| || |_) |
 |_| |_| |_||_||_| |_||_||_____/ |____/   2022
'''


def search_between(s, first, last):
    '''
    Search in 's' for the substring that is between 'first' and 'last'
    '''
    try:
        start = s.index( first ) + len( first )
        end = s.index( last, start )
    except:
        return
    return s[start:end].strip()

def in_paren(qsplit, ind):
    '''
    Split string on space and return whether the item in index 'ind' is inside a parentheses
    '''
    return qsplit[:ind].count('(')>qsplit[:ind].count(')')

def create_query_plan(query, keywords, action):
    '''
    Given a query, the set of keywords that we expect to pe present and the overall action, return the query plan for this query.

    This can and will be used recursively
    '''

    dic = {val: None for val in keywords if val!=';'}

    ql = [val for val in query.split(' ') if val !='']

    kw_in_query = []
    kw_positions = []
    i=0
    while i<len(ql):
        if in_paren(ql, i):
            i+=1
            continue
        if ql[i] in keywords:
            kw_in_query.append(ql[i])
            kw_positions.append(i)

        elif i!=len(ql)-1 and f'{ql[i]} {ql[i+1]}' in keywords:
            kw_in_query.append(f'{ql[i]} {ql[i+1]}')
            ql[i] = ql[i]+' '+ql[i+1]
            ql.pop(i+1)
            kw_positions.append(i)
        i+=1
    
    for i in range(len(kw_in_query)-1):
        dic[kw_in_query[i]] = ' '.join(ql[kw_positions[i]+1:kw_positions[i+1]])

    if action == 'create view':
        dic['as'] = interpret(dic['as'])

    if action=='select':
        dic = evaluate_from_clause(dic)

        if dic['where'] is not None:
            dic = evaluate_where_clause(dic)

        if dic['distinct'] is not None:
            dic['select'] = dic['distinct']
            dic['distinct'] = True

        if dic['order by'] is not None:
            dic['from'] = dic['from']
            if 'desc' in dic['order by']:
                dic['desc'] = True
            else:
                dic['desc'] = False
            dic['order by'] = dic['order by'].removesuffix(' asc').removesuffix(' desc')

        else:
            dic['desc'] = None
    
    if action=='create table':
        args = dic['create table'][dic['create table'].index('('):dic['create table'].index(')')+1]
        dic['create table'] = dic['create table'].removesuffix(args).strip()
        arg_nopk = args.replace('primary key', '')[1:-1]

        arglist = [val.strip().split(' ') for val in arg_nopk.split(',')]

        dic['column_names'] = ','.join([val[0] for val in arglist])
        dic['column_types'] = ','.join([val[1] for val in arglist])

        if 'primary key' in args:
            arglist = args[1:-1].split(' ')
            dic['primary key'] = arglist[arglist.index('primary')-2]
        else:
            dic['primary key'] = None
        
        # Check if there are unique keywords
        if 'unique' in args:
            arglist = args[1:-1].split(' ')
            # Get the column names
            column_names = [arglist[i-2] for i, kw in enumerate(arglist) if kw.rstrip(',') == 'unique']
            dic['unique'] = ','.join(column_names)
        else:
            dic['unique'] = None
    
    if action == 'delete from':
        if dic['where'] is not None:
            dic = evaluate_where_clause(dic)
    
    if action=='import':
        dic = {'import table' if key=='import' else key: val for key, val in dic.items()}

    if action=='insert into':
        if dic['values'][0] == '(' and dic['values'][-1] == ')':
            dic['values'] = dic['values'][1:-1]
        else:
            raise ValueError('Your parens are not right m8')

    if action=='unlock table':
        if dic['force'] is not None:
            dic['force'] = True
        else:
            dic['force'] = False

    if action=='create index':
        on_value = dic["on"].split(" ")

        if len(on_value) == 4 and on_value[1] == '(' and on_value[-1]==')':
            dic['on'] = on_value[0]
            dic['column'] = on_value[2]
    
    return dic

def evaluate_from_clause(dic):
    '''
    Evaluate the part of the query (argument or subquery) that is supplied as the 'from' argument
    '''
    join_types = ['inner', 'left', 'right', 'full', 'sm', 'inl']
    from_split = dic['from'].split(' ')
    if from_split[0] == '(' and from_split[-1] == ')':
        subquery = ' '.join(from_split[1:-1])
        dic['from'] = interpret(subquery)

    join_idx = [i for i,word in enumerate(from_split) if word=='join' and not in_paren(from_split,i)]
    on_idx = [i for i,word in enumerate(from_split) if word=='on' and not in_paren(from_split,i)]
    if join_idx:
        join_idx = join_idx[0]
        on_idx = on_idx[0]
        join_dic = {}
        if from_split[join_idx-1] in join_types:
            join_dic['join'] = from_split[join_idx-1]
            join_dic['left'] = ' '.join(from_split[:join_idx-1])
        else:
            join_dic['join'] = 'inner'
            join_dic['left'] = ' '.join(from_split[:join_idx])
        join_dic['right'] = ' '.join(from_split[join_idx+1:on_idx])
        join_dic['on'] = ''.join(from_split[on_idx+1:])

        if join_dic['left'].startswith('(') and join_dic['left'].endswith(')'):
            join_dic['left'] = interpret(join_dic['left'][1:-1].strip())

        if join_dic['right'].startswith('(') and join_dic['right'].endswith(')'):
            join_dic['right'] = interpret(join_dic['right'][1:-1].strip())

        dic['from'] = join_dic

    return dic

def has_parenth(list):
    return list[0]=='(' and list[-1]==')'

def evaluate_where_clause(dic):
    '''
    Evaluate the part of the query that is supplied as the 'where' argument
    '''
    where_clause = dic['where']
    where_split = where_clause.split(' ')
    if len(where_split) == 1:
        where_dic = where_clause
    else:
        where_dic = form_where_clause(where_clause)
    
    dic['where'] = where_dic

    return dic

def form_where_clause(where_split):
    '''
    Evaluate the recursive part of the where clause. Returns a dictionary
    '''
    logical_operators = ['and', 'or', 'between', 'not']

    if(type(where_split) != list):
        where_split = where_split.split(' ')

    operator_idx = [i for i,word in enumerate(where_split) if word in logical_operators and not in_paren(where_split,i)]

    if len(operator_idx) == 0 and has_parenth(where_split):
        while has_parenth(where_split):
            where_split = where_split[1:-1]
        operator_idx = [i for i,word in enumerate(where_split) if word in logical_operators and not in_paren(where_split,i)]

    if len(operator_idx) == 0:
        return ''.join(where_split)
    
    if (where_split[operator_idx[0]]) == "between":
            left = where_split[operator_idx[0]+1]
            right = where_split[operator_idx[0]+3]
            left = where_split[operator_idx[0]-1] + ">=" + left
            right = where_split[operator_idx[0]-1] + "<=" + right
            where_split[operator_idx[0]+1] = left
            where_split[operator_idx[0]+3] = right
            del where_split[0]
            del where_split[0]

    operator_idx_f = operator_idx[0]
    where_dic = {}
    left = where_split[:operator_idx_f]
    right = where_split[operator_idx_f+1:]

    if (where_split[operator_idx[0]]) == "not":
        left = None
    elif has_parenth(left):
        left = form_where_clause(left)
    else:
        left = ' '.join(left)

    if has_parenth(right) or operator_idx_f is not None:
        right = form_where_clause(right)
    else:
        right = ' '.join(right)      

    where_dic['left'] = left
    where_dic['operator'] = ''.join(where_split[operator_idx_f])
    where_dic['right'] = right
            
    return where_dic

def interpret(query):
    '''
    Interpret the query.
    '''
    kw_per_action = {'create table': ['create table'],
                     'drop table': ['drop table'],
                     'cast': ['cast', 'from', 'to'],
                     'import': ['import', 'from'],
                     'export': ['export', 'to'],
                     'insert into': ['insert into', 'values'],
                     'select': ['select', 'from', 'where', 'distinct', 'order by', 'limit'],
                     'lock table': ['lock table', 'mode'],
                     'unlock table': ['unlock table', 'force'],
                     'delete from': ['delete from', 'where'],
                     'update table': ['update table', 'set', 'where'],
                     'create index': ['create index', 'on', 'column', 'using'],
                     'drop index': ['drop index'],
                     'create view' : ['create view', 'as']
                     }

    if query[-1]!=';':
        query+=';'

    query = query.replace("(", " ( ").replace(")", " ) ").replace(";", " ;").strip()

    for kw in kw_per_action.keys():
        if query.startswith(kw):
            action = kw

    return create_query_plan(query, kw_per_action[action]+[';'], action)

def execute_dic(dic):
    '''
    Execute the given dictionary
    '''
    
    for key in dic.keys():
        # Skip the where key
        if key != 'where' and isinstance(dic[key],dict):
            dic[key] = execute_dic(dic[key])

    action = list(dic.keys())[0].replace(' ','_')
    
    return getattr(db, action)(*dic.values())


def interpret_meta(command):
    """
    Interpret meta commands. These commands are used to handle DB stuff, something that can not be easily handled with mSQL given the current architecture.

    The available meta commands are:

    lsdb - list databases
    lstb - list tables
    cdb - change/create database
    rmdb - delete database
    """
    action = command.split(' ')[0].removesuffix(';')

    db_name = db._name if search_between(command, action,';')=='' else search_between(command, action,';')

    verbose = True
    if action == 'cdb' and ' -noverb' in db_name:
        db_name = db_name.replace(' -noverb','')
        verbose = False

    def list_databases(db_name):
        [print(fold.removesuffix('_db')) for fold in os.listdir('dbdata')]

    def list_tables(db_name):
        [print(pklf.removesuffix('.pkl')) for pklf in os.listdir(f'dbdata/{db_name}_db') if pklf.endswith('.pkl')\
            and not pklf.startswith('meta')]

    def change_db(db_name):
        global db
        db = Database(db_name, load=True, verbose=verbose)

    def remove_db(db_name):
        shutil.rmtree(f'dbdata/{db_name}_db')

    commands_dict = {
        'lsdb': list_databases,
        'lstb': list_tables,
        'cdb': change_db,
        'rmdb': remove_db,
    }

    commands_dict[action](db_name)


if __name__ == "__main__":
    fname = os.getenv('SQL')
    dbname = os.getenv('DB')

    db = Database(dbname, load=True)

    if fname is not None:
        for line in open(fname, 'r').read().splitlines():
            if line.startswith('--'): continue
            if line.startswith('explain'):
                dic = interpret(line.removeprefix('explain '))
                pprint(dic, sort_dicts=False)
            if line.startswith('convert'):
                dic = convert_to_RA(interpret(line.removeprefix('convert ')))
                pprint(dic, sort_dicts=False)
            else:
                dic = interpret(line.lower())
                result = execute_dic(dic)
                if isinstance(result,Table):
                    result.show()


    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory


    print(art)
    session = PromptSession(history=FileHistory('.inp_history'))
    while 1:
        try:
            line = session.prompt(f'({db._name})> ', auto_suggest=AutoSuggestFromHistory()).lower()
            if line[-1]!=';':
                line+=';'
        except (KeyboardInterrupt, EOFError):
            print('\nbye!')
            break
        try:
            if line=='exit;':
                break
            if line.split(' ')[0].removesuffix(';') in ['lsdb', 'lstb', 'cdb', 'rmdb']:
                interpret_meta(line)
            elif line.startswith('explain'):
                dic = interpret(line.removeprefix('explain '))
                pprint(dic, sort_dicts=False)
            elif line.startswith('convert'):
                RA_expression = convert_to_RA(interpret(line.removeprefix('convert ')))
                print(RA_expression)
            else:
                dic = interpret(line)
                result = execute_dic(dic)
                if isinstance(result,Table):
                    result.show()
        except Exception:
            print(traceback.format_exc())


