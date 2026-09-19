import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const localPython = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.SUPPORTOPS_PYTHON || (existsSync(localPython) ? localPython : 'python');
const child = spawn(python, ['-m', 'scripts.e2e_server'], {
  cwd: root,
  stdio: 'inherit',
  windowsHide: true,
  env: process.env,
});
child.on('error', error => { console.error(error.message); process.exit(1); });
child.on('exit', code => process.exit(code ?? 1));
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => child.kill(signal));
}
