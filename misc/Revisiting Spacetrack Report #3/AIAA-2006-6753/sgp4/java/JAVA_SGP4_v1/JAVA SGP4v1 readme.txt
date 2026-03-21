 * 
 * JAVA SGP4 v1 readme
 *
 * This version was converted about the time of the baseline 2006 paper, but the code was reasonably close
 * to the final 2006 version. As a result, the structure differs a little from the 2006 (and subsequent) versions. 
 * Another reader made some minor changes in the SatElset.java file. Both files are preserved with the original 
 * having a .old extension added.
 *
 * Code notes:
 * Conversion to Java by Joe Coughlin at joe.coughlin@mslco.com in Jan 2007
 * 
 * current : 26 jul 05 david vallado fixes for paper note that each fix is
 *                     preceded by a comment with "sgp4fix" and an explanation of what was changed
 * changes : 10 aug 04 david vallado 2nd printing baseline working 
 *           14 may 01 david vallado 2nd edition baseline 
 *                  97 nasa internet version 
 *                  80 norad original baseline
 * 
    We tested the version that I wrote against David's code at the time and the largest differences 
    were on the centimeter level and usually down at computer round off. The differences could usually 
    be attributed to the input time and millisecond round-off issues. We've kept it updated with changes, 
    but they have never been posted. The version I wrote is compatible with most IDEs - I personally use Eclipse.

 *
 * Egemen Imre fix
 * 2008
 *
    I started looking into SGP4-Java code and I thought I should send you small bugfixes rather than a bunch of 
    virtually rewritten codes. I'm sending you an updated version of the SatElset file where I made three major 
    changes (and marked them clearly with "FIX" with the old lines commented out)

	Fixes to the Oct 2005 version (as of 02 Jun 2008):
		- Decimal point is a point for US but is a comma for many other locales. This issue with non-US locales is fixed 
		  by changing dfXdotY and dfdotX variables in SatElset.java. US locale is now explicitly forced when handling 
		  these variables.
		- A redundant card1 definition at thend of getCard1() is deleted
		- The nDotDot and BStar variables, when zero, are defined as 00000-0 in the TLE card (line 1). However the code 
		  in getCard1() would convert these to 00000+0 which would then change the checksum (+ is 0 and - is 1 in the 
		  checksum). 
	The nDotDot and BStar in getCard1() are modified so that if the exp is 0, it is explicitly set to "-0".

