# Diseñador de etiquetas Zebra (ZPL II)

Editor visual para diseñar etiquetas y obtener el código ZPL listo para enviar a impresoras Zebra.

## Instalación y uso

```
pip install -r requirements.txt
python main.py          (o doble clic en ZebraDesigner.bat)
```

## Funciones

- **Lienzo**: arrastrar para mover, tiradores para redimensionar, flechas para mover 1 dot (Shift = 10),
  Ctrl+rueda para zoom, rejilla con ajuste (mm o dots), menú contextual con clic derecho.
- **Elementos**: texto (fuentes 0 y bitmap A–V, rotación, bloque ^FB con ajuste/alineación, ^FR),
  imágenes (archivo o portapapeles, umbral/tramado/invertir → ^GFA comprimido), rectángulos (^GB, redondeo),
  líneas horizontales/verticales/diagonales (^GD), círculos y elipses (^GC/^GE).
- **Códigos de barras** (todos los de ZPL II con vista previa real vía zint):
  Code 128, Code 39, Code 93, Code 11, Interleaved / Industrial / Standard 2 of 5, Codabar, LOGMARS, MSI,
  Plessey, EAN-13, EAN-8, UPC-A, UPC-E, TLC39, POSTNET, Planet, PDF417, MicroPDF417, CODABLOCK F,
  Code 49, QR, Data Matrix, Aztec, MaxiCode y GS1 DataBar.
- **Etiqueta**: tamaño en mm o pulgadas, 152/203/300/600 dpi, copias (^PQ), oscuridad (^MD), velocidad (^PR),
  origen (^LH), impresión invertida (^POI), espejo, UTF-8 (^CI28).
- **Salida**: pestaña *Código ZPL* en vivo, copiar, exportar `.zpl`, imprimir por red (TCP 9100) o a una
  impresora instalada en Windows (RAW), y vista previa exacta online con Labelary (F5).
- **Archivos**: los diseños se guardan como `.zlabel` (JSON con las imágenes incrustadas). Deshacer/rehacer.

La vista previa local está calibrada contra el render de Labelary; para validar al 100 % antes de imprimir
usa *Vista Labelary* (envía el ZPL a api.labelary.com, no lo uses con datos confidenciales).
