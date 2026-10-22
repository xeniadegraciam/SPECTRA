"""Export unnormalized absorption spectra stored in Athena project files.
Run from the SPECTRA directory after placing the three .prj files there.
Existing processed JSON files are not changed.
"""
from pathlib import Path
import ast
import gzip
import json
import re
import shutil
import numpy as np

# 📂 Place this script and Athena projects in the SPECTRA folder.
main_folder = Path(__file__).resolve().parent
export_folder = main_folder / 'dashboard_export'
index_file = export_folder / 'database_index.json'
projects = {'As': 'As_refs_USB.prj', 'Fe': 'Fe_NSLSII_references.prj', 'S': 'S_refs_athena.prj'}

# 🔎 Match labels without treating punctuation as scientifically meaningful.
def normalized_name(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())

# 🔎 Read the embedded Athena record arrays without changing their values.
def parse_project(project_path):
    with gzip.open(project_path, 'rt', encoding='latin-1') as project_stream:
        project_text = project_stream.read()
    records = []
    for section in project_text.split('[record]'):
        args_match = re.search(r'@args\s*=\s*\((.*?)\);', section, re.S)
        x_match = re.search(r'@x\s*=\s*\((.*?)\);', section, re.S)
        y_match = re.search(r'@y\s*=\s*\((.*?)\);', section, re.S)
        if not all((args_match, x_match, y_match)):
            continue
        # ✅ Perl Athena arrays here contain quoted strings and numeric scalars.
        args_list = ast.literal_eval('[' + args_match.group(1) + ']')
        arguments = dict(zip(args_list[::2], args_list[1::2]))
        energies = np.asarray(ast.literal_eval('[' + x_match.group(1) + ']'), dtype=float)
        absorption = np.asarray(ast.literal_eval('[' + y_match.group(1) + ']'), dtype=float)
        records.append((arguments, energies, absorption))
    return records

# 📋 Load the existing index, preserving all spectroscopy links.
index = json.loads(index_file.read_text(encoding='utf-8'))
if not isinstance(index, list):
    raise ValueError('database_index.json must contain a JSON array')

# ⚠️ Stop rather than silently overwrite an ambiguous reference assignment.
seen = set()
report = []
for element, project_filename in projects.items():
    project_path = main_folder / project_filename
    if not project_path.exists():
        raise FileNotFoundError(f'Missing Athena project: {project_path}')
    project_records = parse_project(project_path)
    project_output = export_folder / element / 'athena_projects'
    project_output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(project_path, project_output / project_filename)
    candidates = [row for row in index if row.get('element') == element]
    for arguments, energies, absorption in project_records:
        label = str(arguments.get('label', ''))
        matches = [row for row in candidates if normalized_name(row.get('athena_group', row['sample_id'])) == normalized_name(label)]
        if len(matches) != 1:
            report.append({'element': element, 'athena_label': label, 'status': 'unmatched_or_ambiguous', 'matches': len(matches)})
            continue
        row = matches[0]
        sample_id = row['sample_id']
        if sample_id in seen:
            raise ValueError(f'Duplicate sample ID: {sample_id}')
        seen.add(sample_id)
        if len(energies) != len(absorption) or len(energies) < 2 or not np.all(np.isfinite(energies)) or not np.all(np.isfinite(absorption)):
            report.append({'element': element, 'athena_label': label, 'status': 'invalid_numeric_arrays'})
            continue
        output_folder = export_folder / element / 'unnormalized'
        output_folder.mkdir(parents=True, exist_ok=True)
        output_file = output_folder / (sample_id + '_mu.dat')
        np.savetxt(output_file, np.column_stack([energies, absorption]), fmt='%.12g', delimiter='\t',
                   header=f'Energy_eV\tmu\nAthena label: {label}\nData: stored Athena x/y arrays; not original detector counts', comments='# ')
        row['unnormalized_download'] = output_file.relative_to(export_folder).as_posix()
        row['athena_project_download'] = (project_output / project_filename).relative_to(export_folder).as_posix()
        row['original_source_path_from_athena'] = str(arguments.get('source', ''))
        row['acquisition_file_verified'] = False
        # ⚠️ A project filename alone does not verify the beamline.
        if element == 'Fe':
            row['facility'] = 'NSLS-II (project-name inference)'
        elif element == 'As':
            row['facility'] = 'SSRL (reported; verify per reference)'
        elif element == 'S':
            row['facility'] = 'Unverified (mixed provenance possible)'
        row.setdefault('beamline', 'Unverified')
        report.append({'element': element, 'athena_label': label, 'sample_id': sample_id, 'status': 'exported', 'points': len(energies)})

# 💾 Update the index and produce an auditable reconciliation report.
index_file.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding='utf-8')
(export_folder / 'athena_export_audit.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
print('Exported:', sum(row['status'] == 'exported' for row in report), '/', len(report))
print('Review:', [row for row in report if row['status'] != 'exported'])
