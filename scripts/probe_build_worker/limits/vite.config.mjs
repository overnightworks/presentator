// Slidev loads this file from the deck root while `slidev build` runs, so the
// hook below runs as the build user, in the step's process. The probe's web
// client writes the case into `limit.txt` of the run's copy of this deck before
// the STEP; after the bundle is written the hook adds what the case names to
// the talk, and the worker's own checks then judge the reply. The case file
// names the case and the two counts one over the bounds, which the client reads
// from the web's own settings and the wire codec.
import fs from 'node:fs'
import path from 'node:path'

const CASE_FILE = '/work/deck/limit.txt'
// `many-entries` stays under the entries bound and takes seconds to send.
const MANY_ENTRIES = 90_000
const MEGABYTE = 1024 * 1024

let outDirectory = ''

function manyFiles(count) {
  const directory = path.join(outDirectory, 'files')
  fs.mkdirSync(directory)
  for (let index = 0; index < count; index += 1) {
    fs.writeFileSync(path.join(directory, String(index)), 'x')
  }
}

function manyMegabytes(count) {
  const block = Buffer.alloc(MEGABYTE)
  for (let index = 0; index < count; index += 1) {
    fs.writeFileSync(path.join(outDirectory, `block-${index}`), block)
  }
}

const plugin = {
  name: 'limits',
  configResolved(config) {
    outDirectory = path.resolve(config.root, config.build.outDir)
  },
  closeBundle() {
    const given = JSON.parse(fs.readFileSync(CASE_FILE, 'utf8'))
    if (given.mode === 'many-entries') {
      manyFiles(MANY_ENTRIES)
    } else if (given.mode === 'output-entries') {
      manyFiles(given.output_entries)
    } else if (given.mode === 'output-megabytes') {
      manyMegabytes(given.output_megabytes)
    } else if (given.mode === 'output-shape') {
      fs.symlinkSync('/etc/passwd', path.join(outDirectory, 'link'))
    }
  },
}

export default { plugins: [plugin] }
