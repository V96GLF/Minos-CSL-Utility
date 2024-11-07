# Minos-CSL-Utility
# Version 0.7

A utility to combine CSL, ADIF, EDI and Minos files into a new CSL file.

This python utility creates a Minos Archive .csl file, based on the input of one or more files with any mix of type CSL, ADI, ADIF, EDI or Minos.

From version 0.6 there are three methods of adding data, selected under "Merge Options":

"Keep all records" does exactly what it says, except that identical records are ignored. If the same callsign has been given a different locator, then both records will be retained.

"Keep most recent" - if the callsign matches, then the most recent record is kept, overwriting previous records. For example, if the same callsign has changed locator, then the most recent locator is kept and the older locator is deleted.

"Smart Merge" combines records that have an identical callsign, using the most recent data to overwrite older data. But if the newer record has a blank field, then the data from the older record is retained. For example, if a station's locator has changed, then the new locator will be kept - but if the operator's name was in the comments field of the older record and the newer record has a blank comment field, then the name from the older record will be retained.

Callsigns are only considered identical if they match fully. Hence G2ABC, GW2ABC and G2ABC/P are treated as different callsigns.

Records that contain a callsign only (i.e. no locator, exchange or comment) can be removed, if the "Remove callsign-only records" box is checked.

Merging happens in the order of adding the files. Therefore, add the oldest file first.

Version 0.7 changes
- bug fix on comments field of .adi files
- loading file progress indicator, so that it doesn't look like it has gone to sleep
- slight change to UI layout

Author: Russell Whitworth G4CTP russell_whitworth@hotmail.com
(with a lot of help from Claude.AI)
