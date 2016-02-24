from lxml import etree
from sys import argv, exit

generate_cpp = False
generate_hpp = False
xml_file = None
arg_error = False

for arg in argv[1:]:
    if arg == '-c': generate_cpp = True
    elif arg == '-h': generate_hpp = True
    elif xml_file is None: xml_file = arg
    else: arg_error = True
if not generate_hpp and not generate_cpp: arg_error = True
if arg_error:
    print('usage: %s [-h] [-c] <xml-file>' % argv[0])
    exit(1)

tree = etree.parse(xml_file)
root = tree.getroot()

if root.tag != 'plugin':
    print('malformed XML input')
    exit(2)

autogen_notice = '// This file is generated automatically!\n// Do NOT edit!\n\n'

hpp = autogen_notice
hpp += '''
#include "luaFunctionData.h"
#include "v_repLib.h"
#include <boost/assign/list_of.hpp>

void registerLuaStuff();

'''
cpp = autogen_notice
cpp += '''
#include "stubs.h"
#include <boost/assign/list_of.hpp>
#include <boost/lexical_cast.hpp>

'''
cppreg = '''void registerLuaStuff()
{
    std::vector<int> inArgs;
'''

pluginName = root.attrib['name']
author = root.attrib['author']

commandPrefix = 'simExt%s_' % pluginName

def c_type(param, subtype=False):
    t = param.attrib['item-type'] if subtype else param.attrib['type'] 
    if t == 'table': return 'std::vector<%s>' % c_type(param, True)
    if t == 'int': return 'int'
    if t == 'float': return 'float'
    if t == 'string': return 'std::string'
    if t == 'bool': return 'bool'

def c_field(param):
    return '%s %s' % (c_type(param), param.attrib['name'])

def c_struct(name, params, constructor=False):
    cons = '\n    %s();' % name if constructor else ''
    return 'struct %s\n{\n    %s;%s\n};' %  (name, ';\n    '.join(c_field(p) for p in params), cons)

def vrep_type(param, subtype=False):
    t = param.attrib['item-type'] if subtype else param.attrib['type'] 
    if t == 'table': return 'sim_lua_arg_table|%s' % vrep_type(param, True)
    if t == 'int': return 'sim_lua_arg_int'
    if t == 'float': return 'sim_lua_arg_float'
    if t == 'string': return 'sim_lua_arg_string'
    if t == 'bool': return 'sim_lua_arg_bool'

def vrep_help_type(param):
    t = param.attrib['type'] 
    if t == 'table': return 'table' + ('_%d' % param.attrib['minsize'] if 'minsize' in param.attrib else '')
    if t == 'int': return 'number'
    if t == 'float': return 'number'
    if t == 'string': return 'string'
    if t == 'bool': return 'bool'

def lfda(param, subtype=False):
    t = param.attrib['item-type'] if subtype else param.attrib['type'] 
    suffix = '' if subtype else '[0]'
    if t == 'table': return lfda(param, True)
    if t == 'int': return 'intData' + suffix
    if t == 'float': return 'floatData' + suffix
    if t == 'string': return 'stringData' + suffix
    if t == 'bool': return 'boolData' + suffix

def c_defval(param):
    d = param.attrib['default']
    if param.attrib['type'] == 'table':
        d = 'boost::assign::list_of' + ''.join(map(lambda x: '(%s)' % x.strip(), d.strip()[1:-1].split(',')))
    return d

def lua_command(command):
    return commandPrefix + command.attrib['name']

for enum in root.findall('enum'):
    enumName = enum.attrib['name']
    prefix = enum.attrib['item-prefix'] if 'item-prefix' in enum.attrib else ''
    base = int(enum.attrib['base']) if 'base' in enum.attrib else None
    hpp += 'enum %s\n{\n' % enumName
    enum2str_cases = []
    for item in enum.findall('item'):
        itemName = prefix + item.attrib['name']
        hpp += '    %s%s,\n' % (itemName, ' = %d' % base if base else '')
        base = None
        cppreg += '''    simRegisterCustomLuaVariable("{itemName}", (boost::lexical_cast<std::string>({itemName})).c_str());
'''.format(**locals())
        enum2str_cases.append('case {itemName}: return "{itemName}";'.format(**locals()))
    enum2str_cases.append('default: return "???";')
    hpp += '};\n\n'
    hpp += 'const char * %s_string(%s x);\n\n' % (enumName.lower(), enumName)
    cpp += '''
const char * %s_string(%s x)
{
    switch(x)
    {
        %s
    }
}
''' % (enumName.lower(), enumName, '\n        '.join(enum2str_cases))
        
for cmd in root.findall('command'):
    cmdName = cmd.attrib['name']
    params = cmd.findall('params/param')
    mandatory_params = [p for p in params if 'default' not in p.attrib]
    optional_params = [p for p in params if 'default' in p.attrib]
    returns = cmd.findall('return/param')
    in_struct = c_struct('%s_in' % cmdName, params, constructor=True)
    out_struct = c_struct('%s_out' % cmdName, returns, constructor=True)
    set_in_defaults = ''.join('\n    %s = %s;' % (p.attrib['name'], c_defval(p)) for p in params if 'default' in p.attrib)
    set_out_defaults = ''.join('\n    %s = %s;' % (p.attrib['name'], c_defval(p)) for p in returns if 'default' in p.attrib)
    cpp += '''
{cmdName}_in::{cmdName}_in()
{{{set_in_defaults}
}}

{cmdName}_out::{cmdName}_out()
{{{set_out_defaults}
}}
'''.format(**locals())
    n = len(params)
    nm = len(mandatory_params)
    in_args = ',\n    '.join('%s, %d' % (vrep_type(p), p.attrib.get('minsize', 0)) for p in params)
    get_in_args = '\n        '.join(
        ['in_args.%s = inData->at(%d).%s;' % (p.attrib['name'], i, lfda(p)) for i, p in enumerate(mandatory_params)]
        + ['''if(inData->size()>{j}) in_args.{name} = inData->at({j}).{lfda};'''.format(j=nm+i, name=p.attrib['name'], lfda=lfda(p), default=c_defval(p)) for i, p in enumerate(optional_params)]
    )
    set_out_args = '\n        '.join('D.pushOutData(CLuaFunctionDataItem(out_args.%s));' % p.attrib['name'] for p in returns)
    hpp += '''
{in_struct}

{out_struct}

void {cmdName}(SLuaCallBack *p, {cmdName}_in *in, {cmdName}_out *out);

void {cmdName}(SLuaCallBack *p, const char *cmd, {cmdName}_in *in, {cmdName}_out *out);
'''.format(**locals())
    c_arglist = ', '.join(c_field(p) for p in params)
    if c_arglist: c_arglist = ', ' + c_arglist
    c_arglist_def = ', '.join(c_field(p) + (' = %s' % c_defval(p) if 'default' in p.attrib else '') for p in params)
    if c_arglist_def: c_arglist_def = ', ' + c_arglist_def
    fill_struct = ';\n    '.join('in_args.{n} = {n}'.format(n=p.attrib['name']) for p in params)
    if len(returns) == 1:
        rt = c_type(returns[0])
        rn = returns[0].attrib['name']
        singleReturn = '''{rt} {cmdName}(SLuaCallBack *p{c_arglist})
{{
    {cmdName}_in in_args;
    {fill_struct};
    {cmdName}_out out_args;
    {cmdName}(p, &in_args, &out_args);
    return out_args.{rn};
}}

'''.format(**locals())
        hpp += '''
{rt} {cmdName}(SLuaCallBack *p{c_arglist_def});
'''.format(**locals())
    else:
        singleReturn = ''

    hpp += '''
void {cmdName}(SLuaCallBack *p, {cmdName}_out *out{c_arglist_def});
'''.format(**locals())
    cpp += '''
const int inArgs_{cmdName}[] = {{
    {n},
    {in_args}
}};

{singleReturn}void {cmdName}(SLuaCallBack *p, {cmdName}_out *out{c_arglist})
{{
    {cmdName}_in in_args;
    {fill_struct};
    {cmdName}(p, &in_args, out);
}}

void {cmdName}(SLuaCallBack *p, {cmdName}_in *in, {cmdName}_out *out)
{{
    {cmdName}(p, "{commandPrefix}{cmdName}", in, out);
}}

void LUA_{cmdName}_CALLBACK(SLuaCallBack *p)
{{
    p->outputArgCount = 0;
    CLuaFunctionData D;
    if(D.readDataFromLua(p, inArgs_{cmdName}, {nm}, "{commandPrefix}{cmdName}"))
    {{
        std::vector<CLuaFunctionDataItem>* inData = D.getInDataPtr();
        {cmdName}_in in_args;
        {cmdName}_out out_args;
        {get_in_args}
        {cmdName}(p, "{commandPrefix}{cmdName}", &in_args, &out_args);
        {set_out_args}
    }}
    D.writeDataToLua(p);
}}
'''.format(**locals())
    help_out_args = ','.join('%s %s' % (vrep_help_type(p), p.attrib['name']) for p in returns)
    help_in_args = ','.join('%s %s' % (vrep_help_type(p), p.attrib['name']) + ('=%s' % p.attrib['default'] if 'default' in p.attrib else '') for p in params)
    cppreg += '''
    CLuaFunctionData::getInputDataForFunctionRegistration(inArgs_{cmdName}, inArgs);
    simRegisterCustomLuaFunction("{commandPrefix}{cmdName}", "{help_out_args}={commandPrefix}{cmdName}({help_in_args})", &inArgs[0], LUA_{cmdName}_CALLBACK);
'''.format(**locals())

for fn in root.findall('script-function'):
    fnName = fn.attrib['name']
    params = fn.findall('params/param')
    returns = fn.findall('return/param')
    in_struct = c_struct('%s_in' % fnName, params)
    out_struct = c_struct('%s_out' % fnName, returns)
    n = len(returns)
    out_args = ',\n    '.join('%s, %d' % (vrep_type(p), p.attrib.get('minsize', 0)) for p in returns)
    set_in_args = '\n    '.join('D.pushOutData_luaFunctionCall(CLuaFunctionDataItem(in->%s));' % p.attrib['name'] for p in params)

    get_out_args = '\n            '.join('out->%s = outData->at(%d).%s;' % (p.attrib['name'], i, lfda(p)) for i, p in enumerate(returns))
    hpp += '''
{in_struct}

{out_struct}

bool {fnName}(simInt scriptId, const char *func, {fnName}_in *in, {fnName}_out *out);
'''.format(**locals())
    cpp += '''
const int outArgs_{fnName}[] = {{
    {n},
    {out_args}
}};

bool {fnName}(simInt scriptId, const char *func, {fnName}_in *in, {fnName}_out *out)
{{
    SLuaCallBack c;
    CLuaFunctionData D;
    bool ret = false;

    {set_in_args}
    D.writeDataToLua_luaFunctionCall(&c, outArgs_{fnName});

    if(simCallScriptFunction(scriptId, func, &c, NULL) != -1)
    {{
        if(D.readDataFromLua_luaFunctionCall(&c, outArgs_{fnName}, outArgs_{fnName}[0], func))
        {{
            std::vector<CLuaFunctionDataItem> *outData = D.getOutDataPtr_luaFunctionCall();
            {get_out_args}
            ret = true;
        }}
        else
        {{
            simSetLastError(func, "return value size and/or type is incorrect");
        }}
    }}
    else
    {{
        simSetLastError(func, "callback returned an error");
    }}

    D.releaseBuffers_luaFunctionCall(&c);
    return ret;
}}

'''.format(**locals())

cppreg += '''
}
'''
cpp += cppreg

if generate_hpp: print(hpp)
if generate_cpp: print(cpp)
