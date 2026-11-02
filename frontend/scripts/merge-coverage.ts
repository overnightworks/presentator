// Adds the drive's coverage to vitest's on the same source files, writes the
// lobby's lcov file for SonarCloud at the repository root's paths, and fails
// unless the merged branches are all reached. Vitest alone has no threshold,
// because that would ask for tests that mirror what the drive already runs.
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import libCoverage, { type CoverageMapData } from 'istanbul-lib-coverage';
import libReport from 'istanbul-lib-report';
import libSourceMaps from 'istanbul-lib-source-maps';
import reports from 'istanbul-reports';

const reportsDirectory = fileURLToPath(new URL('../../reports/lobby-coverage', import.meta.url));
const repositoryRoot = fileURLToPath(new URL('../..', import.meta.url));

function read(path: string): CoverageMapData {
	return JSON.parse(readFileSync(path, 'utf8')) as CoverageMapData;
}

// The instrumented build counts positions in the code the build made; the
// source map each file carries puts them back on the source files vitest
// reports.
const drive = libCoverage.createCoverageMap({});
for (const file of readdirSync(`${reportsDirectory}/drive`)) {
	drive.merge(read(`${reportsDirectory}/drive/${file}`));
}
const merged = libCoverage.createCoverageMap(read(`${reportsDirectory}/coverage-final.json`));
const remapped = await libSourceMaps.createSourceMapStore().transformCoverage(drive);
// Remapping ends a line's last range at Infinity, which vitest's JSON file
// holds as null; written the same way, equal ranges are counted as one.
merged.merge(libCoverage.createCoverageMap(JSON.parse(JSON.stringify(remapped)) as CoverageMapData));

const context = libReport.createContext({ dir: reportsDirectory, coverageMap: merged });
reports.create('lcovonly', { file: 'lcov-lobby.info', projectRoot: repositoryRoot }).execute(context);
reports.create('text').execute(context);

const { branches } = merged.getCoverageSummary();
if (branches.pct !== 100) {
	console.error(`The lobby's branches are ${branches.covered} of ${branches.total} reached by vitest and the drive together.`);
	process.exit(1);
}
