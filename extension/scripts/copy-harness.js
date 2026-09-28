const fs = require('fs');
const path = require('path');

const source = path.resolve(__dirname, '..', '..', 'harness');
const target = path.resolve(__dirname, '..', 'python', 'harness');

fs.rmSync(target, { recursive: true, force: true });
fs.mkdirSync(target, { recursive: true });
for (const file of fs.readdirSync(source)) {
  if (file.endsWith('.py')) {
    fs.copyFileSync(path.join(source, file), path.join(target, file));
  }
}
console.log(`Copied harness into ${target}`);
