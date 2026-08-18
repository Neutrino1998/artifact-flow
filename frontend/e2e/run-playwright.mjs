import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

async function reservePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  if (!address || typeof address === 'string') {
    server.close();
    throw new Error('Could not allocate a Playwright web-server port');
  }
  await new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
  return address.port;
}

const frontendDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const cliPath = path.join(frontendDir, 'node_modules', '@playwright', 'test', 'cli.js');
const port = await reservePort();
const child = spawn(process.execPath, [cliPath, 'test', ...process.argv.slice(2)], {
  cwd: frontendDir,
  env: {
    ...process.env,
    ARTIFACTFLOW_PLAYWRIGHT_PORT: String(port),
  },
  stdio: 'inherit',
});

child.once('error', (error) => {
  console.error(error);
  process.exitCode = 1;
});
child.once('exit', (code, signal) => {
  process.exitCode = signal ? 1 : (code ?? 1);
});
