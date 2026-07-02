/*
 * QCAD autostart script: convert DWG input to SVG output.
 *
 * Uses Community Edition compatible APIs (direct RFileImporter, RFileExporter).
 * This is the same as qcad_dwg_to_pdf.js but explicitly named for SVG output.
 *
 * Usage from CLI: qcad -no-gui -autostart <this-file> -- <input> <output.svg>
 */

function main() {
    if (typeof args === "undefined" || !args || args.length < 2) {
        qWarning("Missing input/output arguments for DWG->SVG conversion");
        if (typeof qApp !== "undefined") { qApp.exit(1); }
        return;
    }

    var inputPath = args[0];
    var outputPath = args[1];

    // Create document and interface (works in both Community and Pro editions)
    var storage = new RMemoryStorage();
    var spatialIndex = new RSpatialIndexSimple();
    var doc = new RDocument(storage, spatialIndex);
    var di = new RDocumentInterface(doc);

    // Import DWG using direct RFileImporter (Community Edition compatible)
    var importer = new RFileImporter(di, inputPath);
    if (isNull(importer)) {
        qWarning("Failed to create importer for: " + inputPath);
        if (typeof qApp !== "undefined") { qApp.exit(1); }
        return;
    }

    var success = importer.importFile();
    if (!success) {
        qWarning("Failed to import DWG: " + inputPath);
        if (typeof qApp !== "undefined") { qApp.exit(1); }
        return;
    }

    // Export as SVG using direct RFileExporter (Community Edition supports SVG export)
    var exporter = new RFileExporter(di, outputPath, "SVG");
    if (isNull(exporter)) {
        qWarning("Failed to create SVG exporter for: " + outputPath);
        if (typeof qApp !== "undefined") { qApp.exit(1); }
        return;
    }

    success = exporter.exportFile();
    if (!success) {
        qWarning("Failed to export SVG: " + outputPath);
        if (typeof qApp !== "undefined") { qApp.exit(1); }
        return;
    }

    qDebug("QCAD Community DWG->SVG conversion done: " + inputPath + " -> " + outputPath);
}

main();