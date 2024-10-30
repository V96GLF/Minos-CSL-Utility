# Minos-CSL-Utility
A utility to combine CSL, ADIF, EDI and Minos files into a new CSL file.

This python utility creates a Minos Archive .csl file, based on the input of one or more files with any mix of type CSL, ADI, ADIF, EDI or Minos.

If "Smart Merge" is not checked, then identical records are merged (i.e. de-duped), but if there is a difference in any field then multiple records are created. So, for example, if G2ABC has been logged in square IO91 and IO92, then both records are retained.

If "Smart Merge" is checked, then records with the same callsign are combined. So, for example, if G2ABC has been logged in square IO91RF and later in square IO92MA, then only the IO92MA record is retained. However, if the earlier record contained an exchange but the later record did not, then the earlier exchange is retained. So, for example, if the following two records are combined...

G2ABC, IO91RF, GF

G2ABC, IO92MA, <blank>

...then the merged output will be...

G2ABC, IO92MA, GF

Callsigns are only considered identical if they match fully. Hence G2ABC, GW2ABC and G2ABC/P are treated as different callsigns.

Merging happens in the order of adding the files. Therefore, add the oldest file first.

Author: Russell Whitworth G4CTP russell_whitworth@hotmail.com
(with a lot of help from Claude.AI)

Version 0.4 initial release