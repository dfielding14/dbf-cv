%-----------------------------------------------------------------------------
% Class options
%

\ProvidesClass{cv}[2026/03/07]
\NeedsTeXFormat{LaTeX2e}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{article}}
\ProcessOptions\relax
\LoadClass{article}
\RequirePackage{iftex}
\ifluatex\else
  \ClassError{cv}{This CV must be built with LuaLaTeX}{Use `python -m dbf_cv build`.}
\fi

\RequirePackage[margin=1in]{geometry}
\RequirePackage{hyperref}
\RequirePackage{enumitem}
\RequirePackage[dvipsnames]{xcolor}
\RequirePackage{fontspec}
\RequirePackage{titlesec}

%-----------------------------------------------------------------------------
% Colors
%

\definecolor{body}{HTML}{000000}
\definecolor{sections}{HTML}{004181}
\definecolor{subsections}{HTML}{666666}
\definecolor{rules}{HTML}{CCCCCC}
\definecolor{links}{HTML}{006767}
\definecolor{deemph}{HTML}{666666}
\definecolor{highlight}{HTML}{006767}

%-----------------------------------------------------------------------------
% Font
%

\defaultfontfeatures{Ligatures=TeX, Scale=MatchLowercase}
\InputIfFileExists{build/generated/font_profile.tex}{}{
  \ClassError{cv}{Missing build/generated/font_profile.tex}{Run `python -m dbf_cv build` first.}
}

%-----------------------------------------------------------------------------
% Main document heading
%

\newcommand{\headersection}[2]{%
    {%
        \fontsize{20pt}{20pt}%
        \cvnamefont\selectfont #1%
        \hfill%
        \color{sections}\cvtitlefont\selectfont #2%
    }%
}

%-----------------------------------------------------------------------------
% Section formatting
%

\setlength{\parindent}{0pt}
\setlength{\parskip}{\baselineskip}

\titlespacing{\section}{0pt}{0pt}{0pt}
\titlespacing{\subsection}{0pt}{0pt}{0pt}

\titleformat{\section}
  {\vspace{-0.2em}\color{sections}\cvsectionfont\fontsize{14pt}{16pt}\selectfont\raggedright}
  {}{0em}{}
  [{\vspace{-0.1em}\titlerule[0.2pt]\vspace{1em}}]

\titleformat{\subsection}
  {\color{subsections}\cvsubsectionfont\fontsize{14pt}{16pt}\selectfont}
  {}{0em}{}
  [{\vspace{0.4em}}]

\renewenvironment{itemize}{
  \begin{list}{}{
    \setlength{\leftmargin}{2em}
    \setlength{\itemsep}{0.em}
    \setlength{\parskip}{0pt}
    \setlength{\parsep}{0.25em}
  }
}{
  \end{list}
}
