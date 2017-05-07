#!/usr/bin/env python
import argparse
import json
import os
import re
import shlex
import subprocess

def parse_compile_command(cmd, dir):
  opts = shlex.split(cmd)
  includes = []
  defines = []
  for opt in opts:
    # TODO(m): Add support for more compilers (this is GCC/clang syntax).
    if opt.startswith('-I'):
      includes.append(os.path.abspath(os.path.join(dir, opt[2:].strip())))
    elif opt.startswith('-D'):
      defines.append(opt[2:].strip())

  return (includes, defines)

def gcc_get_included_files(cmd, dir):
  opts = shlex.split(cmd)
  for i in range(len(opts) - 1, 0, -1):
    opt = opts[i].strip()
    if opt == '':
      # Drop empty items.
      opts.pop(i)
    if opt == '-c':
      # We only do the preprocessing step.
      opts[i] = '-E'
    elif opt == '-o' and (i + 1) < len(opts):
      # Do not ouptut anything.
      # TODO(m): Support Windows.
      opts[i + 1] = '/dev/null'
    elif opt in ['-M', '-MM', '-MG', '-MP', '-MD', '-MMD']:
      # Drop dependency generation.
      opts.pop(i)
    elif opt in ['-MF', '-MT', '-MQ'] and (i + 1) < len(opts):
      # Drop dependency generation.
      opts.pop(i + 1)
      opts.pop(i)
  # Add the -H option to output all included header files.
  opts.append('-H')

  try:
    res = subprocess.check_output(opts, stderr=subprocess.STDOUT, cwd=dir)
    files = []
    lines = res.split('\n')
    re_prg = re.compile('^\.+ ')
    for line in lines:
      if re_prg.match(line):
        file_name = line[(line.index(' ') + 1):].strip()
        # TODO(m): Check if the file is located inside the source or build folder instead.
        if not os.path.isabs(file_name):
          file_name = os.path.abspath(os.path.join(dir, file_name))
          files.append(file_name)
    return list(set(files))
  except:
    print 'FAIL'
    return []

def collect_header_files(cmd, dir):
  # Collect all included header files for this complie unit.
  # TODO(m): Here we assume that we're running a compiler that understands GCC options.
  header_files = gcc_get_included_files(cmd, dir)
  return header_files

def load_compile_db(file_name):
  with open(file_name, 'r') as file:
    db_json = json.loads(file.read())
  file_db = []
  found_headers = set()
  for item in db_json:
    # Get the directory for this compile unit (all paths are releative to this dir).
    dir = item['directory']

    # Get the include dirs and defines for this compile unit.
    (include_dirs, defines) = parse_compile_command(item['command'], dir)

    # Add the source file to the db.
    src_file = os.path.abspath(os.path.join(dir, item['file']))
    file_db.append({ 'src': src_file, 'include_dirs': include_dirs, 'defines': defines})

    # Collect header files for this source file.
    header_files = collect_header_files(item['command'], dir)
    for header_file in header_files:
      if header_file not in found_headers:
        found_headers.add(header_file)
        # TODO(m): Here we just pick the include dirs and defines for the current compile unit.
        # Duplicate includes of this specific header file (from other compile units) will be
        # excluded, and so we will not know other include dirs/defines combinations for this
        # header file. Maybe we should do a union of sorts?
        file_db.append({ 'src': header_file, 'include_dirs': include_dirs, 'defines': defines})
  return file_db

def collect_meson_files(src_dir):
  meson_files = []
  for root, dir_names, file_names in os.walk(src_dir):
    for file_name in file_names:
      if file_name == 'meson.build':
        meson_files.append(os.path.abspath(os.path.join(root, file_name)))
  return meson_files

def mesonintrospect(commands, build_dir):
  args = ['mesonintrospect.py']
  args.extend(commands)
  return subprocess.check_output(args, stderr=subprocess.STDOUT, cwd=build_dir)

def get_project_name(build_dir):
  info = json.loads(mesonintrospect(['--projectinfo'], build_dir))
  return info['name']

def make_valid_filename(s):
  valid_chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-'
  return ''.join(c for c in s if c in valid_chars)

def generator_qtcreator(build_dir, src_dir):
  # Read the compile database.
  file_db = load_compile_db(os.path.join(build_dir, 'compile_commands.json'))

  project_name = make_valid_filename(get_project_name(build_dir))

  # Generate the .creator file.
  creator_file = os.path.join(build_dir, project_name + '.creator')
  with open(creator_file, 'w') as file:
    file.write('[General]')

  # Generate the .config file.
  config_file = os.path.join(build_dir, project_name + '.config')
  defines = []
  for item in file_db:
    defines.extend(item['defines'])
  defines = sorted(list(set(defines)))
  with open(config_file, 'w') as file:
    file.write('// Add predefined macros for your project here. For example:\n')
    file.write('// #define THE_ANSWER 42\n')
    for item in defines:
      file.write('#define %s\n' % item)

  # Generate the .files file.
  files_file = os.path.join(build_dir, project_name + '.files')
  files = []
  for item in file_db:
    files.append(item['src'])
  files.extend(collect_meson_files(src_dir))
  files = sorted(list(set(files)))
  with open(files_file, 'w') as file:
    for item in files:
      file.write(os.path.relpath(item, build_dir) + '\n')

  # Generate the .includes file.
  includes_file = os.path.join(build_dir, project_name + '.includes')
  includes = []
  for item in file_db:
    includes.extend(item['include_dirs'])
  includes = sorted(list(set(includes)))
  with open(includes_file, 'w') as file:
    for item in includes:
      file.write(os.path.relpath(item, build_dir) + '\n')

def is_src_dir(dir):
  return os.path.isfile(os.path.join(dir, 'meson.build'))

def is_build_dir(dir):
  return os.path.isfile(os.path.join(dir, 'build.ninja')) and os.path.isfile(os.path.join(dir, 'compile_commands.json'))

def main():
  parser = argparse.ArgumentParser(description='Generate IDE projects from Meson.')
  parser.add_argument('path', metavar='PATH',
                      help='source or build path')
  args = parser.parse_args()

  # Determine source and build folders.
  if is_src_dir(args.path):
    src_dir = args.path
    build_dir = os.getcwd()
  else:
    src_dir = os.getcwd()
    build_dir = args.path
  if not is_src_dir(src_dir):
    raise ValueError('%s does not appear to be a valid source directory' % src_dir)
  if not is_build_dir(build_dir):
    raise ValueError('%s does not appear to be a valid build directory' % build_dir)

  # Export the IDE project file(s).
  generator_qtcreator(build_dir, src_dir)

if __name__ == "__main__":
  main()
