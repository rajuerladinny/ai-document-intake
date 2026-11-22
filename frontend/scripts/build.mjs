// Builds the static demo into dist/ (deployable to GitHub Pages or any static host).
//   node scripts/build.mjs          production build
//   node scripts/build.mjs --serve  rebuild on change and serve on http://localhost:5173
import { build, context } from 'esbuild';
import { copyFile, mkdir, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const outdir = join(root, 'dist');
const serve = process.argv.includes('--serve');

const options = {
  entryPoints: { main: join(root, 'src/main.tsx') },
  outdir: join(outdir, 'assets'),
  bundle: true,
  format: 'esm',
  target: ['es2020'],
  jsx: 'automatic',
  loader: { '.txt': 'text' },
  minify: !serve,
  sourcemap: serve,
  define: { 'process.env.NODE_ENV': JSON.stringify(serve ? 'development' : 'production') },
  logLevel: 'info',
};

await rm(outdir, { recursive: true, force: true });
await mkdir(outdir, { recursive: true });
await copyFile(join(root, 'index.html'), join(outdir, 'index.html'));

if (serve) {
  const ctx = await context(options);
  await ctx.watch();
  const { port } = await ctx.serve({ servedir: outdir, port: 5173 });
  console.log(`Demo running at http://localhost:${port}`);
} else {
  await build(options);
}
