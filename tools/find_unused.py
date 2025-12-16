#!/usr/bin/env python3
import re
from collections import defaultdict
import sys
import os

def scan_file(path):
    s=open(path,encoding='utf-8').read()
    lines=s.splitlines()
    funcs=[]
    for i,l in enumerate(lines, start=1):
        m=re.match(r'^(\s*)def\s+([A-Za-z_]\w*)\s*\(',l)
        if m:
            indent=len(m.group(1))
            funcs.append((i,m.group(2),indent))
    caps=re.findall(r'^([A-Z][_A-Z0-9]+)\s*=',s,flags=re.M)
    return s, lines, funcs, sorted(set(caps))

def main(paths):
    files=[]
    for p in paths:
        if os.path.isdir(p):
            for root,_,fnames in os.walk(p):
                for fn in fnames:
                    if fn.endswith('.py'):
                        files.append(os.path.join(root,fn))
        elif os.path.isfile(p) and p.endswith('.py'):
            files.append(p)
    if not files:
        print('No python files found for', paths)
        return 1

    repo_text = {}
    per_file = {}
    for f in sorted(files):
        s, lines, funcs, caps = scan_file(f)
        repo_text[f]=s
        per_file[f]={'lines':lines,'funcs':funcs,'caps':caps}

    # report per-file
    all_funcs = defaultdict(list)  # name -> list of (file,line,indent)
    all_caps = defaultdict(list)   # name -> list of files
    for f,d in per_file.items():
        funcs = d['funcs']
        print(f"\nFile: {f}\nFound {len(funcs)} function defs")
        for ln,name,ind in funcs:
            all_funcs[name].append((f,ln,ind))
            # count refs across repo (occurrences minus defs)
            occ = sum(len(re.findall(r'\b'+re.escape(name)+r'\b', repo_text[ff])) for ff in repo_text)
            refs_elsewhere = occ - len(all_funcs[name])  # subtract known defs seen so far; rough
            print(f"{name} @ L{ln:4d} indent={ind} refs_across_repo~={refs_elsewhere}")
        caps = d['caps']
        print(f"Found {len(caps)} UPPERCASE assignments (candidates)")
        for c in caps:
            all_caps[c].append(f)
            occ = sum(len(re.findall(r'\b'+re.escape(c)+r'\b', repo_text[ff])) for ff in repo_text)
            refs_elsewhere = occ - len(all_caps[c])
            print(f"{c} refs_across_repo~={refs_elsewhere}")

    # duplicates
    duplicates = {name:defs for name,defs in all_funcs.items() if len(defs)>1}
    if duplicates:
        print('\nDuplicate function definitions across files:')
        for name,defs in duplicates.items():
            print(f"{name}: {', '.join([f'{os.path.relpath(f)}@L{ln}' for f,ln,_ in defs])}")
    else:
        print('\nNo duplicate function definitions across scanned files.')

    # likely unused functions: no non-def refs across repo
    likely_unused = []
    for name,defs in all_funcs.items():
        occ = sum(len(re.findall(r'\b'+re.escape(name)+r'\b', repo_text[ff])) for ff in repo_text)
        if occ == len(defs):
            for f,ln,_ in defs:
                likely_unused.append((name,f,ln))
    print('\nLikely unused functions (no non-def refs across repo):')
    for name,f,ln in likely_unused:
        print(f"{name} @ {f}:{ln}")

    # likely unused uppercase globals
    likely_unused_caps = []
    for name,files_list in all_caps.items():
        occ = sum(len(re.findall(r'\b'+re.escape(name)+r'\b', repo_text[ff])) for ff in repo_text)
        defs = len(files_list)
        if occ == defs:
            for f in files_list:
                likely_unused_caps.append((name,f))
    print('\nLikely unused UPPERCASE globals (no non-def refs across repo):')
    for name,f in likely_unused_caps:
        print(f"{name} @ {f}")
    return 0


if __name__ == '__main__':
    args = sys.argv[1:] or ['.']
    sys.exit(main(args))
