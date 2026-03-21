 * 
 * JAVA SGP4 v2 readme
 *
 * This version was converted after the baseline 2006 paper was presented, so the code is very similar to the current 
 * code versions today. An output file from the JAVA version was included with the output from the other versions and 
 * they match almost identically. 
 *
 * Code notes:
 * Conversion to Java by Shawn E. Gano in Jun 2009
 *
	I have converted your latest SGP4 code (3 Nov 08) to java.  I did notice that you added a java version in the code 
	download on http://www.centerforspace.com/downloads/ but that package was a little out of date and I didn't really 
	like its API.  So I did pretty much a straight forward conversion of the C++ code to java (with a few minor changes 
	to make it easier in Java since there are no pointers).  I ran all the verification test included and compared the 
	java to the C++ and found the output to be almost identical.  There were usually   just a couple of characters 
	different in the entire files (the number varied from between 1-4 depending on the different gravitational constants 
	and opsmode chosen), and the differences were in the 0.001 cm range.

	I have attached the converted files which can be embedded by anyone into their own API layout or used as is.  

*  Corrections made Jul 21, 2014
*     some non printing characters in the text caused some compiler problems. The degree symbols were eliminated.
	