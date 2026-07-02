/*
 * QCAD autostart script: convert DWG input to DXF output.
 * Uses Community Edition compatible APIs (RFileImporter, RFileExporter).
 * Usage from CLI: qcad -no-gui -autostart <this-file> -- <input> <output>
 */

function main() {
    if (typeof args === "undefined" || !args || args.length < 2) {
        qWarning("Missing input/output arguments for DWG->DXF conversion");
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

    // Import DWG using direct RFileImporter (not registry-based Pro API)
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

    // Export as DXF using direct RFileExporter (works in Community edition)
    var exporter = new RFileExporter(di, outputPath, "DXF");
    if (isNull(exporter)) {
        qWarning("Failed to create DXF exporter for: " + outputPath);
        if (typeof qApp !== "undefined") { qApp.exit(1); }
        return;
    }

    success = exporter.exportFile();
    if (!success) {
        qWarning("Failed to export DXF: " + outputPath);
        if (typeof qApp !== "undefined") { qApp.exit(1); }
        return;
    }

    qDebug("QCAD DWG->DXF conversion done: " + inputPath + " -> " + outputPath);
}

main();