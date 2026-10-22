//+------------------------------------------------------------------+
//|                                             Mt5IntelSignals.mq5  |
//|  MT5 intel - signals. This indicator places no orders.            |
//+------------------------------------------------------------------+
//  Draws the trades the Python console actually took, so the rules can be
//  eyeballed against the chart instead of only read as numbers.
//
//  It deliberately does NOT recompute the strategies. The families live in
//  Python (src/mt5_intel/research/strategies*.py) and stay the only place they
//  are defined; an indicator with its own copy would be free to drift, and a
//  chart that disagreed with the console could not then tell you which of the
//  two was wrong - which is the question this is here to answer.
//
//  So first run the exporter, then attach this indicator:
//
//      .\.venv\Scripts\python.exe -m mt5_intel.research.export_mt5 H1
//
//  It writes three files on the broker's clock, which is the clock the chart
//  draws, and this reads all three from MQL5\Files - named per symbol, so a
//  chart of EURUSD reads EURUSD's trades and never USDJPY's:
//
//    mt5intel_<SYMBOL>_<TF>.csv          the trades - one arrow per position taken
//    mt5intel_<SYMBOL>_<TF>_lines.csv    EMA(50), EMA(200), SMA(20), SMA(50) bar by bar
//    mt5intel_<SYMBOL>_<TF>_context.csv  the backdrop numbers the cards are read against
//
//  The averages arrive as data rather than being recomputed with iMA(): the
//  chart line and the number printed beside it are then one calculation, not
//  two that agree to within a seed.
//
//  Two live cards that entered on the same bar, in the same direction, are the
//  one agreement a chart can show on its own: two rules reading one bar the
//  same way. Those arrows take the agreement colour instead of their family's
//  (green on a dark chart), and their tag carries xN, so the reading survives
//  without the colour.
//
//  Nothing on screen is a mystery: every arrow's colour is named in the key,
//  every arrow carries a tag that key spells out, and the moving averages are
//  ordinary indicator lines - their colour, width and style live in the usual
//  Indicator Properties dialog (Colors tab), like any other indicator. That key
//  is the whole panel: the backdrop numbers it used to repeat are on the Live
//  tab, and a chart that reads them out twice is a chart you read twice.
//
//  When a watched rule takes a position the chart says so out loud as well as
//  drawing it - an alert, pushed to the phone too while InpNotifyPush is on.
//  InpNotifyRules names the rules (trend_pullback by default, "" for none) and
//  the message is the arrow's own reading: "TP is buying", with the symbol, the
//  entry, the stop and the target. Attaching the indicator never rings for the
//  cards already in the file - the first pass only records what is there - so an
//  alert means a position opened while this chart was being watched. It arrives
//  with the arrow, both on the exporter's clock.
//
//  Rows whose bar is not in the loaded history are counted and reported rather
//  than silently dropped, so a missing arrow is explained instead of silent.
//+------------------------------------------------------------------+
#property copyright "MT5 intel (this indicator draws only - it places no orders)"
#property version   "1.14"
#property description "Draws the trades MT5 intel took, with the averages they were read against."
#property description "Export first:  python -m mt5_intel.research.export_mt5 H1 --symbol USDJPY"
#property indicator_chart_window
#property indicator_buffers 4
#property indicator_plots   4
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrDodgerBlue
#property indicator_width1  2
#property indicator_label1  "EMA fast"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrOrangeRed
#property indicator_width2  2
#property indicator_label2  "EMA slow"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrMediumSeaGreen
#property indicator_width3  1
#property indicator_label3  "SMA fast"
#property indicator_type4   DRAW_LINE
#property indicator_color4  clrGoldenrod
#property indicator_width4  1
#property indicator_label4  "SMA slow"

input string InpFile          = "";   // CSV override (empty = mt5intel_<SYMBOL>_<TF>.csv)
input int    InpMaxTrades     = 40;                 // newest N rows to draw (0 = every row)
input bool   InpShowClosed    = true;               // draw finished trades as well as running ones
input bool   InpOnlyRunning   = false;              // hide everything except the running trades
input string InpFamilies      = "";                 // only these families, comma separated ("" = all)
input bool   InpLabelRunning  = true;               // name the running trades on the chart
input int    InpLabelBars     = 60;                 // name arrows this many bars old (0 = running only)
input bool   InpLabelAll      = false;              // name every arrow drawn
input bool   InpShowBarriers  = true;               // entry / SL / TP lines for the running trades
input bool   InpAgreeHighlight = true;              // paint live arrows that agree on one bar
input color  InpAgreeColor    = clrNONE;            // agreement colour (clrNONE = green for the chart)
input bool   InpShowLines     = true;               // the EMA/SMA lines from the exporter
input bool   InpLineEma       = true;               // draw EMA fast/slow
input bool   InpLineSma       = true;               // draw SMA fast/slow
input bool   InpShowRangeBox  = true;               // the fade box that range_extreme_mr trades
input bool   InpShowPanel     = true;               // the colour key (and any fault note above it)
input int    InpReloadSeconds = 30;                 // re-read the CSVs this often (0 = new bar only)
input int    InpArrowOffset   = 10;                 // points between the candle and the arrow
input int    InpFontSize      = 9;                  // panel font size
input string InpNotifyRules   = "trend_pullback";   // alert when these rules take a position ("" = no alert)
input bool   InpNotifyPush    = true;               // push those alerts to the MetaTrader phone app as well

#define PREFIX "MT5INTEL_"
// The columns a row must have to be a trade (family ... bars_held). The agreement column the
// exporter appends after them is optional: a trades file written before it existed still draws,
// just with no arrow painted as agreeing.
#define COLS   11
#define COL_CONFLUENCE 11
// Two live cards, one bar, one direction - what this paints as an agreement.
#define CONFLUENCE_MIN 2

struct Trade
  {
   string   family;
   string   side;
   string   state;
   datetime entry_time;
   double   entry;
   double   sl;
   double   tp;
   double   result_r;
   bool     has_result;
   int      resignals;
   int      bars_held;
   int      confluence;   // live cards that entered this bar, this way (1 = alone)
  };

Trade    g_trades[];
int      g_rows        = 0;
int      g_unmatched   = 0;
bool     g_rows_loaded = false;   // a read has ever succeeded (g_trades is a truthful copy)

// The positions already announced out loud, as the entry bar and the side of each: a reload of a
// file that has not changed has nothing new in it, and the same bar announced twice would read as
// a second signal. g_said_armed is the first pass, which only records - see NotifyNew().
datetime g_said_time[];
string   g_said_side[];
bool     g_said_armed = false;

// The average lines, read from the exporter's lines file and pushed into this
// indicator's own buffers, so the terminal draws them like any other average.
datetime g_line_time[];
double   g_line_ema_fast[], g_line_ema_slow[], g_line_sma_fast[], g_line_sma_slow[];
bool     g_line_fast_ok[], g_line_slow_ok[], g_line_smafast_ok[], g_line_smaslow_ok[];
int      g_line_rows   = 0;
int      g_line_drawn  = 0;
bool     g_lines_file  = false;

double   BufEmaFast[], BufEmaSlow[], BufSmaFast[], BufSmaSlow[];

// The backdrop numbers, as key,value pairs straight from the exporter.
string   g_ctx_key[], g_ctx_val[];
bool     g_ctx_file = false;

bool     g_refill     = true;
int      g_last_bars  = -1;

//+------------------------------------------------------------------+
//| The colour every arrow of a family gets. The key in the panel is   |
//| built from this same function, so a swatch cannot promise a colour  |
//| the arrows on the chart do not use.                                |
//+------------------------------------------------------------------+
color FamilyColor(const string family)
  {
   color palette[12] =
     {
      clrDodgerBlue, clrOrangeRed, clrMediumSeaGreen, clrGoldenrod, clrMediumPurple, clrDeepSkyBlue,
      clrCrimson, clrDarkOrange, clrTeal, clrChocolate, clrSteelBlue, clrOliveDrab
     };
   int h = 0;
   for(int i = 0; i < StringLen(family); i++)
      h = (h * 31 + (int)StringGetCharacter(family, i)) & 0x7FFFFFFF;
   return palette[h % ArraySize(palette)];
  }

//+------------------------------------------------------------------+
//| A short tag for the chart labels: the initials of the rule's words.|
//| The key spells the full name out beside it, so "REM" on a candle is |
//| one glance away from "range_extreme_mr".                           |
//+------------------------------------------------------------------+
string Abbrev(const string family)
  {
   string res = "";
   bool at_word = true;
   for(int i = 0; i < StringLen(family); i++)
     {
      string ch = StringSubstr(family, i, 1);
      if(ch == "_")
        {
         at_word = true;
         continue;
        }
      if(at_word)
        {
         res += ch;
         at_word = false;
        }
     }
   StringToUpper(res);
   return res;
  }

//+------------------------------------------------------------------+
string PadR(const string text, const int width)
  {
   string res = text;
   while(StringLen(res) < width)
      res += " ";
   return res;
  }

//+------------------------------------------------------------------+
string PadL(const string text, const int width)
  {
   string res = text;
   while(StringLen(res) < width)
      res = " " + res;
   return res;
  }

//+------------------------------------------------------------------+
bool OnDarkChart()
  {
   color bg = (color)ChartGetInteger(0, CHART_COLOR_BACKGROUND);
   int r = (int)(bg & 0xFF), g = (int)((bg >> 8) & 0xFF), b = (int)((bg >> 16) & 0xFF);
   return (r + g + b) < 3 * 128;
  }

//+------------------------------------------------------------------+
//| The colour an agreement arrow is painted: two live cards entered    |
//| one bar the same way. Green by default, chosen for the chart it is  |
//| drawn on - one green is not readable on a black chart and a white    |
//| one alike - unless InpAgreeColor names one.                          |
//+------------------------------------------------------------------+
color AgreeColor()
  {
   if(InpAgreeColor != clrNONE)
      return InpAgreeColor;
   return OnDarkChart() ? clrLime : clrGreen;
  }

//+------------------------------------------------------------------+
string ChartTimeframe()
  {
   string name = EnumToString((ENUM_TIMEFRAMES)Period());
   if(StringFind(name, "PERIOD_") == 0)
      name = StringSubstr(name, 7);
   return name;
  }

//+------------------------------------------------------------------+
//| The file this chart reads: its own symbol and its own timeframe,    |
//| so two charts of two symbols cannot draw each other's trades - they |
//| used to, when the files were named by timeframe only and every      |
//| chart opened the same one. An override input names one file by      |
//| hand, and everything (the panel's fault lines included) then says   |
//| exactly what that file claims to be.                                |
//+------------------------------------------------------------------+
string FileName()
  {
   if(StringLen(InpFile) > 0)
      return InpFile;
   return "mt5intel_" + _Symbol + "_" + ChartTimeframe() + ".csv";
  }

//+------------------------------------------------------------------+
string SiblingName(const string base, const string suffix)
  {
   int cut = StringLen(base) - 4;
   if(cut > 0 && StringSubstr(base, cut) == ".csv")
      return StringSubstr(base, 0, cut) + suffix + ".csv";
   return base + suffix + ".csv";
  }

//+------------------------------------------------------------------+
string Context(const string key)
  {
   for(int i = 0; i < ArraySize(g_ctx_key); i++)
      if(g_ctx_key[i] == key)
         return g_ctx_val[i];
   return "?";
  }

//+------------------------------------------------------------------+
//| Is this family one of the names in a comma separated list? The list reads the same whether it
//| came from InpFamilies (what the chart draws) or InpNotifyRules (what it says out loud), so the
//| two cannot disagree about what "trend_pullback" means.
//+------------------------------------------------------------------+
bool NamedIn(const string list, const string family)
  {
   if(StringLen(list) == 0 || StringLen(family) == 0)
      return false;
   string parts[];
   int n = StringSplit(list, (ushort)StringGetCharacter(",", 0), parts);
   for(int i = 0; i < n; i++)
     {
      string wanted = parts[i];
      StringTrimLeft(wanted);
      StringTrimRight(wanted);
      if(StringLen(wanted) > 0 && StringCompare(wanted, family, false) == 0)
         return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Which families the chart draws: all of them unless InpFamilies    |
//| names some.                                                        |
//+------------------------------------------------------------------+
bool FamilyWanted(const string family)
  {
   if(StringLen(InpFamilies) == 0)
      return true;
   return NamedIn(InpFamilies, family);
  }

//+------------------------------------------------------------------+
string Tooltip(const Trade &t, const int index)
  {
   string text = Abbrev(t.family) + "  " + t.family + "\n";
   text += t.side + "  [" + t.state + "]  entry " + DoubleToString(t.entry, _Digits)
           + "  @" + TimeToString(t.entry_time, TIME_DATE | TIME_MINUTES) + "\n";
   text += "SL " + DoubleToString(t.sl, _Digits) + "   TP " + DoubleToString(t.tp, _Digits) + "\n";
   if(t.has_result)
      text += "result " + DoubleToString(t.result_r, 2) + "R\n";
   else
      text += "running, " + IntegerToString(t.bars_held) + " bars in\n";
   if(t.resignals > 0)
      text += "re-signaled " + IntegerToString(t.resignals) + "x while open\n";
   if(t.state == "open" && t.confluence >= CONFLUENCE_MIN)
      text += "agrees with " + IntegerToString(t.confluence - 1)
              + " other live card(s): one bar, one side\n";
   text += "row " + IntegerToString(index + 1) + " of " + IntegerToString(g_rows);
   return text;
  }

//+------------------------------------------------------------------+
//| Has this entry bar, this side, been announced already?            |
//+------------------------------------------------------------------+
bool SaidAlready(const datetime stamp, const string side)
  {
   for(int i = 0; i < ArraySize(g_said_time); i++)
      if(g_said_time[i] == stamp && g_said_side[i] == side)
         return true;
   return false;
  }

//+------------------------------------------------------------------+
//| Say it out loud: a watched rule has taken a position.              |
//|                                                                    |
//| The message opens the way the chart names the rule - the tag the    |
//| arrow carries, then the side - so the alert and the arrow read the  |
//| same thing (TP = trend_pullback). The levels are spelled out rather |
//| than lettered: "TP" here is a rule, and a message reading "TP is     |
//| buying ... TP 157.400" would be one its reader has to decode.       |
//+------------------------------------------------------------------+
void Announce(const Trade &t)
  {
   string text = Abbrev(t.family) + " is " + ((t.side == "buy") ? "buying" : "selling")
                 + "  -  " + _Symbol + " " + ChartTimeframe()
                 + "  entry " + DoubleToString(t.entry, _Digits)
                 + "  stop " + DoubleToString(t.sl, _Digits)
                 + "  target " + DoubleToString(t.tp, _Digits);
   if(t.confluence >= CONFLUENCE_MIN)
      text += "  -  with " + IntegerToString(t.confluence - 1) + " other live card(s)";
   Alert(text);                    // the popup, and a line in the Experts log with it
   if(InpNotifyPush && !SendNotification(text))
      Print("MT5 intel: the alert was shown but not pushed to the phone - set the MetaQuotes ID"
            + " in Tools > Options > Notifications to get these on mobile");
  }

//+------------------------------------------------------------------+
//| Announce a position the moment the file carries it.                |
//|                                                                    |
//| The exporter appends, so a row that was not in the file last time   |
//| is a position that has just opened. The first pass therefore only   |
//| records: attaching the indicator to a file holding a week of cards  |
//| must not ring once per card, and after that a chart being watched   |
//| is the only thing that rings. Only a live card is announced - a row |
//| that appears for a finished trade is history, not a signal.         |
//|                                                                    |
//| This reads the whole file, not the rows the chart draws, so          |
//| InpFamilies and InpMaxTrades cannot silence a rule InpNotifyRules    |
//| asks to hear about.                                                 |
//+------------------------------------------------------------------+
void NotifyNew()
  {
   if(StringLen(InpNotifyRules) == 0)
      return;

   for(int i = 0; i < g_rows && i < ArraySize(g_trades); i++)
     {
      if(g_trades[i].state != "open")
         continue;
      if(!NamedIn(InpNotifyRules, g_trades[i].family))
         continue;
      if(SaidAlready(g_trades[i].entry_time, g_trades[i].side))
         continue;

      int slot = ArraySize(g_said_time);
      ArrayResize(g_said_time, slot + 1);
      ArrayResize(g_said_side, slot + 1);
      g_said_time[slot] = g_trades[i].entry_time;
      g_said_side[slot] = g_trades[i].side;

      if(g_said_armed)              // the first pass is the one that says nothing
         Announce(g_trades[i]);
     }
   g_said_armed = true;
  }

//+------------------------------------------------------------------+
//| Read the trades CSV. Returns the number of data rows, or -1 if     |
//| the file is missing.                                              |
//+------------------------------------------------------------------+
int LoadRows()
  {
   // Parse into a fresh array and commit only after the whole file read cleanly. FileOpen can
   // lose the moment the exporter holds the file open for its rewrite. Freeing g_trades before
   // that left an empty array under the still-counting g_rows, and the next pass walked g_rows
   // into it - "array out of range", a critical error, and with that the indicator stops
   // drawing: everything after that moment, the live pair included, never reaches the chart.
   Trade fresh[];
   int   total = 0;
   ResetLastError();

   int handle = FileOpen(FileName(), FILE_READ | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return -1;

   ushort comma = (ushort)StringGetCharacter(",", 0);

   while(!FileIsEnding(handle))
     {
      string line = FileReadString(handle);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) == 0)
         continue;
      if(total == 0 && StringFind(line, "family") == 0)
         continue;                       // header row

      string parts[];
      if(StringSplit(line, comma, parts) < COLS)
         continue;

      Trade t;
      t.family     = parts[0];
      t.side       = parts[1];
      t.entry_time = StringToTime(parts[2]);
      t.entry      = StringToDouble(parts[3]);
      t.sl         = StringToDouble(parts[4]);
      t.tp         = StringToDouble(parts[5]);
      t.state      = parts[8];
      t.has_result = (StringLen(parts[7]) > 0);
      t.result_r   = t.has_result ? StringToDouble(parts[7]) : 0.0;
      t.resignals  = (int)StringToInteger(parts[9]);
      t.bars_held  = (int)StringToInteger(parts[10]);
      // Optional: a trades file written before the exporter appended the agreement column carries
      // none, and an arrow is then simply alone rather than the whole file being skipped.
      t.confluence = (ArraySize(parts) > COL_CONFLUENCE)
                     ? (int)StringToInteger(parts[COL_CONFLUENCE]) : 1;
      if(t.confluence < 1)
         t.confluence = 1;
      if(t.confluence >= CONFLUENCE_MIN && t.state != "open")
         t.confluence = 1;               // only a running card is live alongside another

      if(t.entry_time <= 0)
         continue;                       // unparseable stamp: skip rather than draw at 1970

      ArrayResize(fresh, total + 1);
      fresh[total] = t;
      total++;
     }
   FileClose(handle);

   // A read that lands while the exporter rewrites the file can see a truncation: it ends
   // cleanly but says nothing. An empty result over data already held is a failed read, so the
   // previous rows stay - the chart keeps its drawing instead of blanking for a reload
   // window. (The exporter now renames whole files over these names, so this is the fallback.)
   if(total == 0 && g_rows > 0)
      return -1;

   // The commit is one move: rows that are on the chart can never be fewer than g_rows claims.
   ArrayFree(g_trades);
   ArrayResize(g_trades, total);
   for(int k = 0; k < total; k++)
      g_trades[k] = fresh[k];
   g_rows        = total;
   g_rows_loaded = true;

   return total;
  }

//+------------------------------------------------------------------+
//| Read the average lines. A blank cell is a bar the average has not  |
//| warmed up on: nothing is drawn there rather than something made up.|
//+------------------------------------------------------------------+
bool LoadLines()
  {
   int handle = FileOpen(SiblingName(FileName(), "_lines"), FILE_READ | FILE_TXT | FILE_ANSI);
   // A failed open (the exporter holds the file for its rewrite) leaves the last good load
   // standing: freeing the arrays here is what let a single missed window blank the averages
   // until the next one.
   if(handle == INVALID_HANDLE)
      return false;

   // Parsed into staging arrays and committed only after the file read cleanly, so a read that
   // caught a mid-rewrite file (ends cleanly, says nothing) cannot blank the lines either.
   datetime f_time[];
   double   f_ema_fast[], f_ema_slow[], f_sma_fast[], f_sma_slow[];
   bool     f_fast_ok[], f_slow_ok[], f_smafast_ok[], f_smaslow_ok[];

   int total = 0;
   ushort comma = (ushort)StringGetCharacter(",", 0);
   while(!FileIsEnding(handle))
     {
      string line = FileReadString(handle);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) == 0)
         continue;
      if(total == 0 && StringFind(line, "time") == 0)
         continue;

      string parts[];
      if(StringSplit(line, comma, parts) < 5)
         continue;
      datetime stamp = StringToTime(parts[0]);
      if(stamp <= 0)
         continue;

      ArrayResize(f_time, total + 1);
      ArrayResize(f_ema_fast, total + 1);
      ArrayResize(f_ema_slow, total + 1);
      ArrayResize(f_sma_fast, total + 1);
      ArrayResize(f_sma_slow, total + 1);
      ArrayResize(f_fast_ok, total + 1);
      ArrayResize(f_slow_ok, total + 1);
      ArrayResize(f_smafast_ok, total + 1);
      ArrayResize(f_smaslow_ok, total + 1);

      f_time[total]      = stamp;
      f_fast_ok[total]    = (StringLen(parts[1]) > 0);
      f_slow_ok[total]    = (StringLen(parts[2]) > 0);
      f_smafast_ok[total] = (StringLen(parts[3]) > 0);
      f_smaslow_ok[total] = (StringLen(parts[4]) > 0);
      f_ema_fast[total]   = f_fast_ok[total]    ? StringToDouble(parts[1]) : 0.0;
      f_ema_slow[total]   = f_slow_ok[total]    ? StringToDouble(parts[2]) : 0.0;
      f_sma_fast[total]   = f_smafast_ok[total] ? StringToDouble(parts[3]) : 0.0;
      f_sma_slow[total]   = f_smaslow_ok[total] ? StringToDouble(parts[4]) : 0.0;
      total++;
     }
   FileClose(handle);

   // Same empty-read rule as the trades: a mid-rewrite window committed nothing.
   if(total == 0 && g_line_rows > 0)
      return false;

   g_line_rows  = total;
   g_lines_file = (total > 0);
   ArrayResize(g_line_time, total);
   ArrayResize(g_line_ema_fast, total);
   ArrayResize(g_line_ema_slow, total);
   ArrayResize(g_line_sma_fast, total);
   ArrayResize(g_line_sma_slow, total);
   ArrayResize(g_line_fast_ok, total);
   ArrayResize(g_line_slow_ok, total);
   ArrayResize(g_line_smafast_ok, total);
   ArrayResize(g_line_smaslow_ok, total);
   for(int k = 0; k < total; k++)
     {
      g_line_time[k]          = f_time[k];
      g_line_ema_fast[k]      = f_ema_fast[k];
      g_line_ema_slow[k]      = f_ema_slow[k];
      g_line_sma_fast[k]      = f_sma_fast[k];
      g_line_sma_slow[k]      = f_sma_slow[k];
      g_line_fast_ok[k]       = f_fast_ok[k];
      g_line_slow_ok[k]       = f_slow_ok[k];
      g_line_smafast_ok[k]    = f_smafast_ok[k];
      g_line_smaslow_ok[k]    = f_smaslow_ok[k];
     }
   g_refill = true;
   return g_lines_file;
  }

//+------------------------------------------------------------------+
//| Read the backdrop numbers.                                        |
//+------------------------------------------------------------------+
bool LoadContext()
  {
   int handle = FileOpen(SiblingName(FileName(), "_context"), FILE_READ | FILE_TXT | FILE_ANSI);
   // Same as the lines: a failed open, or an empty read over data already held, leaves the
   // last good panel standing rather than blanking it for one reload window.
   if(handle == INVALID_HANDLE)
      return false;

   string f_key[], f_val[];
   int total = 0;
   ushort comma = (ushort)StringGetCharacter(",", 0);
   while(!FileIsEnding(handle))
     {
      string line = FileReadString(handle);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) == 0)
         continue;
      if(total == 0 && StringFind(line, "key") == 0)
         continue;

      string parts[];
      if(StringSplit(line, comma, parts) < 2)
         continue;
      ArrayResize(f_key, total + 1);
      ArrayResize(f_val, total + 1);
      f_key[total] = parts[0];
      f_val[total] = parts[1];
      total++;
     }
   FileClose(handle);

   if(total == 0 && ArraySize(g_ctx_key) > 0)
      return false;

   ArrayFree(g_ctx_key);
   ArrayFree(g_ctx_val);
   ArrayResize(g_ctx_key, total);
   ArrayResize(g_ctx_val, total);
   for(int k = 0; k < total; k++)
     {
      g_ctx_key[k] = f_key[k];
      g_ctx_val[k] = f_val[k];
     }
   g_ctx_file = (total > 0);
   return g_ctx_file;
  }

//+------------------------------------------------------------------+
void AddLine(const string name, const datetime from, const datetime to, const double price,
             const color clr, const bool ray, const string tooltip)
  {
   if(!ObjectCreate(0, name, OBJ_TREND, 0, from, price, to, price))
      return;
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DOT);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, ray);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tooltip);
  }

//+------------------------------------------------------------------+
void AddBarrier(const string name, const datetime from, const double price,
                const color clr, const string tooltip)
  {
   AddLine(name, from, iTime(_Symbol, PERIOD_CURRENT, 0), price, clr, true, tooltip);
  }

//+------------------------------------------------------------------+
//| The fade box: the extremes range_extreme_mr measures against. Two  |
//| flat lines over the window the number was taken from, so "price is  |
//| 96% up a 96-bar box" is something you can see rather than infer.    |
//+------------------------------------------------------------------+
void DrawRangeBox(const int rates_total)
  {
   if(!InpShowRangeBox || !g_ctx_file || rates_total < 3)
      return;
   double low  = StringToDouble(Context("range_low"));
   double high = StringToDouble(Context("range_high"));
   int span    = (int)StringToInteger(Context("range_bars"));
   if(low <= 0 || high <= 0 || span < 2)
      return;

   int newest = MathMin(1, rates_total - 1);       // 1 = the last closed bar, as the reading is
   int oldest = MathMin(newest + span, rates_total - 1);
   if(oldest <= newest)
      return;
   datetime from = iTime(_Symbol, PERIOD_CURRENT, oldest);
   datetime to   = iTime(_Symbol, PERIOD_CURRENT, newest);
   if(from <= 0 || to <= 0 || from >= to)
      return;

   color clr = OnDarkChart() ? clrDimGray : clrSilver;
   string tip = IntegerToString(span) + "-bar box - the rule fades a close at "
                + Context("range_pos_pct") + "% of it";
   AddLine(PREFIX + "box_hi", from, to, high, clr, false,
           "box high " + DoubleToString(high, _Digits) + "\n" + tip);
   AddLine(PREFIX + "box_lo", from, to, low, clr, false,
           "box low " + DoubleToString(low, _Digits) + "\n" + tip);

   string label = PREFIX + "box_t";
   if(ObjectCreate(0, label, OBJ_TEXT, 0, to, high))
     {
      ObjectSetString(0, label, OBJPROP_TEXT, " " + IntegerToString(span) + "-bar box");
      ObjectSetString(0, label, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, label, OBJPROP_FONTSIZE, MathMax(6, InpFontSize - 1));
      ObjectSetInteger(0, label, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, label, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
      ObjectSetInteger(0, label, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, label, OBJPROP_HIDDEN, true);
      ObjectSetString(0, label, OBJPROP_TOOLTIP, tip);
     }
  }

//+------------------------------------------------------------------+
//| Push the exported averages into this indicator's own buffers, so    |
//| the terminal draws them as ordinary lines - colour, width and style  |
//| then live in the usual Indicator Properties dialog.                 |
//+------------------------------------------------------------------+
void FillBuffers(const int rates_total)
  {
   g_refill = false;
   ArrayInitialize(BufEmaFast, EMPTY_VALUE);
   ArrayInitialize(BufEmaSlow, EMPTY_VALUE);
   ArrayInitialize(BufSmaFast, EMPTY_VALUE);
   ArrayInitialize(BufSmaSlow, EMPTY_VALUE);

   if(!InpShowLines || g_line_rows == 0 || rates_total <= 0)
      return;

   // The rows are the newest contiguous run of bars, so the newest row is found
   // once and the rest are counted back from it, checking each bar as we go: a
   // history gap stops the line rather than dragging it across the hole.
   int shift0 = iBarShift(_Symbol, PERIOD_CURRENT, g_line_time[0], false);
   if(shift0 < 0 || iTime(_Symbol, PERIOD_CURRENT, shift0) != g_line_time[0])
     {
      g_line_drawn = 0;
      return;
     }

   int drawn = 0;
   for(int k = 0; k < g_line_rows; k++)
     {
      int shift = shift0 - k;
      if(shift < 0)
         break;
      if(iTime(_Symbol, PERIOD_CURRENT, shift) != g_line_time[k])
         break;
      int idx = rates_total - 1 - shift;
      if(idx < 0 || idx >= rates_total)
         break;
      if(InpLineEma)
        {
         if(g_line_fast_ok[k])
            BufEmaFast[idx] = g_line_ema_fast[k];
         if(g_line_slow_ok[k])
            BufEmaSlow[idx] = g_line_ema_slow[k];
        }
      if(InpLineSma)
        {
         if(g_line_smafast_ok[k])
            BufSmaFast[idx] = g_line_sma_fast[k];
         if(g_line_smaslow_ok[k])
            BufSmaSlow[idx] = g_line_sma_slow[k];
        }
      drawn++;
     }
   g_line_drawn = drawn;
  }

//+------------------------------------------------------------------+
void Draw()
  {
   ObjectsDeleteAll(0, PREFIX);
   g_unmatched  = 0;

   int rows = LoadRows();
   if(rows >= 0)
      g_rows_loaded = true;
   else
      rows = g_rows;   // the read lost the race with the writer: keep drawing what we still hold
   if(rows == 0 && !g_rows_loaded)
     {
      Comment("MT5 intel: no " + FileName() + " in MQL5\\Files.\nRun:  "
              + ".venv\\Scripts\\python.exe -m mt5_intel.research.export_mt5 " + ChartTimeframe()
              + " --symbol " + _Symbol);
      ChartRedraw();
      return;
     }
   Comment("");
   // The alerts are not part of what is drawn: a rule the chart is not showing still rings.
   NotifyNew();
   DrawRangeBox(Bars(_Symbol, PERIOD_CURRENT));

   // Ensure every open trade is drawn even when > InpMaxTrades closed rows follow it.
   int first = 0;
   if(InpMaxTrades > 0 && rows > InpMaxTrades)
      first = rows - InpMaxTrades;

   for(int idx = 0; idx < rows; idx++)
     {
      Trade t = g_trades[idx];
      bool running  = (t.state == "open");
      // A current-signal row (state "signal") is what the rule is asking for NOW while it holds
      // a trade: the freshest thing on the file, so it draws regardless of the closed-trade
      // switches, but it is never a taken position and never counts as one.
      bool is_signal = (t.state == "signal");
      if(idx < first && !(running || is_signal))
         continue;
      if(!FamilyWanted(t.family))
         continue;
      if(!running && !is_signal && (!InpShowClosed || InpOnlyRunning))
         continue;

      int shift = iBarShift(_Symbol, PERIOD_CURRENT, t.entry_time, false);
      if(shift < 0 || iTime(_Symbol, PERIOD_CURRENT, shift) != t.entry_time)
        {
         g_unmatched++;
         continue;                        // that bar is not in the chart's loaded history
        }

      double anchor = (t.side == "buy")
                      ? iLow(_Symbol, PERIOD_CURRENT, shift) - InpArrowOffset * _Point
                      : iHigh(_Symbol, PERIOD_CURRENT, shift) + InpArrowOffset * _Point;
      // A live card that entered this bar the same way as another is painted in the agreement
      // colour rather than its family's: the pair is the reading, and green says so at a glance.
      // A current signal keeps its own colour but a hairline width, so a held trade and the
      // reading it is being asked to follow are distinguishable at a glance.
      bool agrees = (InpAgreeHighlight && running && t.confluence >= CONFLUENCE_MIN);
      color clr = agrees ? AgreeColor() : FamilyColor(t.family);
      string name = PREFIX + "a_" + IntegerToString(idx);
      ENUM_OBJECT kind = (t.side == "buy") ? OBJ_ARROW_UP : OBJ_ARROW_DOWN;
      if(ObjectCreate(0, name, kind, 0, t.entry_time, anchor))
        {
         ObjectSetInteger(0, name, OBJPROP_ARROWCODE, (t.side == "buy") ? 233 : 234);
         ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
         ObjectSetInteger(0, name, OBJPROP_WIDTH, running ? 3 : 1);
         ObjectSetInteger(0, name, OBJPROP_ANCHOR, (t.side == "buy") ? ANCHOR_TOP : ANCHOR_BOTTOM);
         ObjectSetString(0, name, OBJPROP_TOOLTIP, Tooltip(t, idx));
        }

      if((running || is_signal) && InpShowBarriers)
        {
         AddBarrier(PREFIX + "en_" + IntegerToString(idx), t.entry_time, t.entry, clrGray,
                    t.family + " entry " + DoubleToString(t.entry, _Digits));
         AddBarrier(PREFIX + "sl_" + IntegerToString(idx), t.entry_time, t.sl, clrCrimson,
                    t.family + " SL " + DoubleToString(t.sl, _Digits));
         AddBarrier(PREFIX + "tp_" + IntegerToString(idx), t.entry_time, t.tp, clrSeaGreen,
                    t.family + " TP " + DoubleToString(t.tp, _Digits));
        }

      // Name the arrow where a name earns its place: the running trades, the
      // recent ones, or all of them when asked. The tag matches the key below.
      bool want_label = (running && InpLabelRunning)
                        || InpLabelAll
                        || (InpLabelBars > 0 && shift <= InpLabelBars);
      if(!want_label)
         continue;

      string label = PREFIX + "t_" + IntegerToString(idx);
      if(!ObjectCreate(0, label, OBJ_TEXT, 0, t.entry_time, anchor))
         continue;
      string text = Abbrev(t.family) + " " + t.side;
      if(t.has_result)
         text += " " + DoubleToString(t.result_r, 1) + "R";
      else if(agrees)
         text += " x" + IntegerToString(t.confluence);   // the reading outlives the colour
      if(running && t.resignals > 0)
         text += " +" + IntegerToString(t.resignals) + "r";
      if(is_signal)
         text += " now";   // a current reading: what the rule asks for while it holds
      ObjectSetString(0, label, OBJPROP_TEXT, " " + text);
      ObjectSetString(0, label, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, label, OBJPROP_FONTSIZE, InpFontSize);
      ObjectSetInteger(0, label, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, label, OBJPROP_ANCHOR,
                       (t.side == "buy") ? ANCHOR_RIGHT_UPPER : ANCHOR_RIGHT_LOWER);
      ObjectSetInteger(0, label, OBJPROP_SELECTABLE, false);
     }
   ChartRedraw();
  }

//+------------------------------------------------------------------+
//| One family's standing: what it is in now, and how its finished      |
//| trades came out. Read from the whole file, not only the drawn rows,  |
//| so a family that is flat still has a colour you can look up.         |
//+------------------------------------------------------------------+
void FamilyStats(const string family, int &running, int &done, double &sum_r,
                 string &now_side, double &now_entry, bool &now_has)
  {
   running = 0;
   done = 0;
   sum_r = 0.0;
   now_side = "";
   now_entry = 0.0;
   now_has = false;
   for(int i = 0; i < g_rows && i < ArraySize(g_trades); i++)
     {
      if(g_trades[i].family != family)
         continue;
      if(g_trades[i].state == "open")
        {
         running++;
         now_side  = g_trades[i].side;
         now_entry = g_trades[i].entry;
         now_has   = true;
        }
      else if(g_trades[i].state == "signal")
        {
         // A current-signal row is neither a running trade nor a finished one: it is the
         // rule's newest standing request, and must not inflate the trade count or the R sum.
         continue;
        }
      else
        {
         done++;
         if(g_trades[i].has_result)
            sum_r += g_trades[i].result_r;
        }
     }
  }

//+------------------------------------------------------------------+
void AddPanelLine(string &texts[], color &clrs[], string &tags[], int &count,
                  const string text, const color clr, const string tag)
  {
   ArrayResize(texts, count + 1);
   ArrayResize(clrs, count + 1);
   ArrayResize(tags, count + 1);
   texts[count] = text;
   clrs[count]  = clr;
   tags[count]  = tag;
   count++;
  }

//+------------------------------------------------------------------+
//| The panel: the key naming every colour and tag that appears on the |
//| chart, with a line for anything that stops the chart telling the   |
//| truth. The backdrop numbers the cards were struck on are not here: |
//| they are on the Live tab, and a chart that reads them out again is  |
//| a chart you have to read twice.                                    |
//+------------------------------------------------------------------+
void DrawPanel()
  {
   ObjectsDeleteAll(0, PREFIX + "l_");
   ObjectsDeleteAll(0, PREFIX + "s_");
   ObjectsDeleteAll(0, PREFIX + "p_");
   if(!InpShowPanel)
      return;

   bool  dark   = OnDarkChart();
   color body   = dark ? (color)C'32,34,38' : clrWhiteSmoke;
   color border = dark ? (color)C'72,76,84' : clrSilver;
   color strong = dark ? clrWhite : clrBlack;
   color weak   = dark ? (color)C'168,172,180' : clrDimGray;
   color warn   = dark ? clrOrange : clrDarkOrange;

   string lines[], tags[];
   color  colors[];
   int    n = 0;

   AddPanelLine(lines, colors, tags, n, "MT5 intel - signals (this indicator places no orders)",
                strong, "");

   // Only a fault earns a line above the key, and there is none in a healthy run: a file from
   // another timeframe, an average that could not be placed, an arrow with no bar to sit on. What
   // this panel will not do is repeat the numbers - see the header.
   string chart_tf = ChartTimeframe();
   string file_tf  = g_ctx_file ? Context("timeframe") : "";
   if(StringLen(file_tf) > 0 && StringLen(file_tf) < 5 && file_tf != chart_tf)
      AddPanelLine(lines, colors, tags, n,
                   "!! this file is " + file_tf + " but the chart is " + chart_tf
                   + " - the arrows are on the wrong bars", warn, "");
   // The files are named per timeframe, not per symbol, so a chart of another symbol can read
   // another symbol's file cleanly - and draw USDJPY entries on EURUSD as if they were its own
   // (the log has caught that alert doing exactly that). Name the mistake in the panel.
   string file_sym = g_ctx_file ? Context("symbol") : "";
   if(StringLen(file_sym) > 0 && StringCompare(file_sym, _Symbol, false) != 0)
      AddPanelLine(lines, colors, tags, n,
                   "!! this file is " + file_sym + " but the chart is " + _Symbol
                   + " - the entries are another symbol's prices", warn, "");
   if(!g_ctx_file)
      AddPanelLine(lines, colors, tags, n,
                   "!! no " + SiblingName(FileName(), "_context")
                   + " - no box, and the file's timeframe cannot be checked. Re-run the exporter",
                   warn, "");
   if(InpShowLines && !g_lines_file)
      AddPanelLine(lines, colors, tags, n,
                   "!! no " + SiblingName(FileName(), "_lines")
                   + " - no average lines. Re-run the exporter to write it", warn, "");
   else if(InpShowLines && g_line_drawn == 0)
      AddPanelLine(lines, colors, tags, n,
                   "!! " + IntegerToString(g_line_rows) + " bars of averages in the file, none of"
                   + " them on this chart - scroll back, or raise Max bars in chart", warn, "");
   if(g_unmatched > 0)
      AddPanelLine(lines, colors, tags, n,
                   "!! " + IntegerToString(g_unmatched)
                   + " rows had no bar in history - scroll back, or raise Max bars in chart",
                   warn, "");

   AddPanelLine(lines, colors, tags, n,
                "key - swatch = arrow colour, tag = arrow name"
                + (InpAgreeHighlight ? ", xN = live cards that agree" : ""), strong, "");

   AddPanelLine(lines, colors, tags, n, "", weak, "");

   // One row per family in the file, in the order the file lists them.
   int    first_row = n;
   string seen      = "";
   for(int i = 0; i < g_rows && i < ArraySize(g_trades); i++)
     {
      string family = g_trades[i].family;
      if(!FamilyWanted(family))
         continue;
      if(StringFind(seen, "|" + family + "|") >= 0)
         continue;
      seen += "|" + family + "|";

      int    running = 0, done = 0;
      double sum_r = 0.0, now_entry = 0.0;
      string now_side = "";
      bool   now_has = false;
      FamilyStats(family, running, done, sum_r, now_side, now_entry, now_has);

      // StringToUpper uppercases in place and returns a bool, so its result is not the string:
      // concatenating it put "true" at the head of this column (the compiler says so).
      StringToUpper(now_side);
      string now  = now_has ? now_side + " " + DoubleToString(now_entry, _Digits) : "flat";
      string mean = (done > 0) ? DoubleToString(sum_r / done, 2) + "R" : "-";
      AddPanelLine(lines, colors, tags, n,
                   "     " + PadR(Abbrev(family), 5) + PadR(family, 20) + PadR(now, 17)
                   + PadL(IntegerToString(running) + " run", 7)
                   + PadL(IntegerToString(done) + " done", 9) + PadL(mean, 7),
                   FamilyColor(family), family);
     }
   if(n == first_row)
      AddPanelLine(lines, colors, tags, n,
                   "     no family in this file matches InpFamilies = \"" + InpFamilies + "\"",
                   warn, "");

   int widest = 24;
   for(int i = 0; i < n; i++)
      widest = MathMax(widest, StringLen(lines[i]));
   int width  = widest * 7 + 30;
   int height = 12 + n * 14;

   string back = PREFIX + "p_back";
   if(ObjectCreate(0, back, OBJ_RECTANGLE_LABEL, 0, 0, 0))
     {
      ObjectSetInteger(0, back, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, back, OBJPROP_XDISTANCE, 6);
      ObjectSetInteger(0, back, OBJPROP_YDISTANCE, 14);
      ObjectSetInteger(0, back, OBJPROP_XSIZE, width);
      ObjectSetInteger(0, back, OBJPROP_YSIZE, height);
      ObjectSetInteger(0, back, OBJPROP_BGCOLOR, body);
      ObjectSetInteger(0, back, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, back, OBJPROP_COLOR, border);
      ObjectSetInteger(0, back, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, back, OBJPROP_BACK, false);
      ObjectSetInteger(0, back, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, back, OBJPROP_HIDDEN, true);
     }

   for(int i = 0; i < n; i++)
     {
      string name = PREFIX + "l_" + IntegerToString(i);
      if(!ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0))
         continue;
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 14);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 22 + i * 14);
      ObjectSetString(0, name, OBJPROP_TEXT, lines[i]);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, InpFontSize);
      ObjectSetInteger(0, name, OBJPROP_COLOR, colors[i]);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }

   // The swatches: the colour of a family's arrows, made by the same function.
   for(int i = 0; i < n; i++)
     {
      if(StringLen(tags[i]) == 0)
         continue;
      string name = PREFIX + "s_" + IntegerToString(i);
      if(!ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0))
         continue;
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 16);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 24 + i * 14);
      ObjectSetInteger(0, name, OBJPROP_XSIZE, 8);
      ObjectSetInteger(0, name, OBJPROP_YSIZE, 8);
      ObjectSetInteger(0, name, OBJPROP_BGCOLOR, FamilyColor(tags[i]));
      ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, name, OBJPROP_COLOR, FamilyColor(tags[i]));
      ObjectSetInteger(0, name, OBJPROP_BACK, false);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   IndicatorSetString(INDICATOR_SHORTNAME, "MT5 intel signals (" + FileName() + ")");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);

   SetIndexBuffer(0, BufEmaFast, INDICATOR_DATA);
   SetIndexBuffer(1, BufEmaSlow, INDICATOR_DATA);
   SetIndexBuffer(2, BufSmaFast, INDICATOR_DATA);
   SetIndexBuffer(3, BufSmaSlow, INDICATOR_DATA);
   // Explicitly oldest-first, the same order as the arrays OnCalculate is given.
   ArraySetAsSeries(BufEmaFast, false);
   ArraySetAsSeries(BufEmaSlow, false);
   ArraySetAsSeries(BufSmaFast, false);
   ArraySetAsSeries(BufSmaSlow, false);
   for(int i = 0; i < 4; i++)
      PlotIndexSetDouble(i, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   ArrayInitialize(BufEmaFast, EMPTY_VALUE);
   ArrayInitialize(BufEmaSlow, EMPTY_VALUE);
   ArrayInitialize(BufSmaFast, EMPTY_VALUE);
   ArrayInitialize(BufSmaSlow, EMPTY_VALUE);

   LoadRows();
   LoadContext();
   LoadLines();

   // Label the lines with the periods the rules actually used, rather than a
   // number typed in here that could drift from the exporter's.
   if(g_lines_file && InpShowLines)
     {
      string tf_label = " " + Context("timeframe");
      PlotIndexSetString(0, PLOT_LABEL, "EMA " + Context("ema_fast_period") + tf_label);
      PlotIndexSetString(1, PLOT_LABEL, "EMA " + Context("ema_slow_period") + tf_label);
      PlotIndexSetString(2, PLOT_LABEL, "SMA " + Context("sma_fast_period") + tf_label);
      PlotIndexSetString(3, PLOT_LABEL, "SMA " + Context("sma_slow_period") + tf_label);
      PlotIndexSetInteger(0, PLOT_DRAW_TYPE, InpLineEma ? DRAW_LINE : DRAW_NONE);
      PlotIndexSetInteger(1, PLOT_DRAW_TYPE, InpLineEma ? DRAW_LINE : DRAW_NONE);
      PlotIndexSetInteger(2, PLOT_DRAW_TYPE, InpLineSma ? DRAW_LINE : DRAW_NONE);
      PlotIndexSetInteger(3, PLOT_DRAW_TYPE, InpLineSma ? DRAW_LINE : DRAW_NONE);
     }
   else
      for(int i = 0; i < 4; i++)
         PlotIndexSetInteger(i, PLOT_DRAW_TYPE, DRAW_NONE);

   g_last_bars = -1;    // the first OnCalculate draws, once history is really loaded
   g_refill    = true;
   if(InpReloadSeconds > 0)
      EventSetTimer(InpReloadSeconds);
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   ObjectsDeleteAll(0, PREFIX);
   Comment("");
   ChartRedraw();
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   LoadRows();
   LoadContext();
   LoadLines();
   Draw();
   DrawPanel();
   FillBuffers(Bars(_Symbol, PERIOD_CURRENT));
   ChartRedraw();
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated,
                const datetime &time[], const double &open[], const double &high[],
                const double &low[], const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
  {
   // A new bar can change which rows are drawable, so the objects are rebuilt
   // then too; the average lines only need refilling when that or a reload moves them.
   bool new_bars = (rates_total != g_last_bars);
   if(new_bars)
     {
      g_last_bars = rates_total;
      g_refill = true;
     }
   // The lines are placed before the panel is drawn, so the panel's line about them is measured
   // against this paint: filled after it, the first paint of a chart would say that none of the
   // averages could be placed - the one reading that is alarming rather than merely stale.
   if(g_refill)
      FillBuffers(rates_total);
   if(new_bars)
     {
      Draw();
      DrawPanel();
     }
   return rates_total;
  }
//+------------------------------------------------------------------+
