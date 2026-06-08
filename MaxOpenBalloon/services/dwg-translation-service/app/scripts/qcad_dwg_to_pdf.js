/*
 * QCAD autostart script: convert DWG input to PDF output.
 * Usage from CLI: qcad -no-gui -autostart <this-file> -- <input> <output>
 */

include("scripts/Pro/ImportExport/ImportFile/ImportFile.js");
include("scripts/Pro/File/SaveAs/SaveAs.js");

function main() {
    if (typeof args === "undefined" || !args || args.length < 2) {
        qWarning("Missing input/output arguments for DWG->PDF conversion");
        return;
    }

    var inputPath = args[0];
    var outputPath = args[1];

    var importIo = new RFileImporterRegistry();
    var importer = importIo.createFileImporter(inputPath, "", "");
    if (isNull(importer)) {
        qWarning("QCAD importer creation failed for input: " + inputPath);
        return;
    }

    var di = RDocumentInterface.createDocument(
        RStorage.createObject(),
        new RSpatialIndexSimple(),
        false
    );

    importer.importFile(di, RFileImporter.ImportAll);

    var filter = RFileExporterRegistry.getFilterByName("PDF");
    var exporter = RFileExporterRegistry.createFileExporter(di, outputPath, filter);
    if (isNull(exporter)) {
        qWarning("QCAD exporter creation failed for output: " + outputPath);
        return;
    }

    exporter.exportFile();
    qDebug("QCAD conversion done: " + inputPath + " -> " + outputPath);
}

main();
