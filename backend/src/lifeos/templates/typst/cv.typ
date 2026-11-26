// CV in one column: name, headline, contacts and profile with a photo on the right,
// then sections with a rule, entries with the period on the right and tight bullets.
// All content comes from sys.inputs.data (JSON); nothing personal lives in this file.
// `sp` scales every vertical gap: the code raises it until the last page is filled, so the
// content is spread evenly over the page(s).
#let d = json(bytes(sys.inputs.at("data")))
#let p = d.personal
#let cv = d.cv
#let layout = cv.at("layout", default: (:))
#let sp = float(sys.inputs.at("spread", default: "1"))
#let size = layout.at("font_size", default: 10.0) * 1pt
#let titles = cv.at("sections", default: (
  experience: "Arbeitserfahrung", education: "Ausbildung", more: "Fähigkeiten und Interessen",
))

#set document(title: p.name + " – Lebenslauf", author: p.name)
#set page(paper: "a4", margin: (top: 1.45cm, bottom: 1.35cm, left: 1.8cm, right: 1.8cm))
#set text(font: "XCharter", size: size, lang: "de", hyphenate: false)
#set par(justify: false, leading: 0.68em, spacing: 0.68em)
#show link: it => it

#let bar = h(4pt) + [|] + h(4pt)

#let section(title) = block(
  above: 0.45cm * sp,
  below: 0.25cm * sp,
  sticky: true,
  width: 100%,
  inset: (bottom: 2.5pt),
  stroke: (bottom: 0.4pt),
  text(weight: "bold", title),
)

#let points(items) = for b in items {
  block(above: 0.16cm * sp, below: 0pt, grid(
    columns: (10pt, 1fr),
    text(size: 8pt)[•],
    if b.at("url", default: none) != none { link(b.url, b.text) } else { b.text },
  ))
}

#let entry(main, period, items: ()) = block(above: 0.3cm * sp, below: 0pt, breakable: true, {
  grid(columns: (1fr, 5cm), column-gutter: 0.2cm, main, align(right, period))
  if items.len() > 0 { v(0.05cm * sp) }
  points(items)
})

// ---- header ----
#grid(
  columns: (68%, 1fr),
  [
    #text(size: 24pt, weight: "bold", p.name)
    #v(-0.3em)
    #text(weight: "bold", cv.headline)
    #v(0.35em)
    #{
      let parts = (p.street + ", " + p.city,)
      if p.email != "" { parts.push(link("mailto:" + p.email, p.email)) }
      if p.phone != "" { parts.push(link("tel:" + p.phone_link, p.phone)) }
      if p.github != "" { parts.push(link("https://" + p.github, p.github)) }
      if p.linkedin != "" { parts.push(link("https://" + p.linkedin, p.linkedin)) }
      parts.join(bar)
    }
    #v(4mm)
    #line(length: 100%, stroke: 0.4pt)
    #v(1mm)
    #text(size: 9pt, style: "italic", cv.profile)
    #v(1mm)
    #line(length: 100%, stroke: 0.4pt)
  ],
  if d.photo != none {
    align(center + top, move(dy: -4mm, image(d.photo, width: 37mm)))
  },
)
#v(4mm * sp)

// ---- work ----
#section(titles.experience)
#for pos in cv.positions {
  entry(
    [*#pos.title*, #pos.company#if pos.place != "" [ -- #pos.place]],
    pos.period,
    items: pos.bullets,
  )
}

// ---- education ----
#section(titles.education)
#for e in cv.education {
  let head = if e.inline {
    [*#e.institution*, #e.degree]
  } else {
    [*#e.institution* \ #e.degree#if e.detail != "" [ (#e.detail)]]
  }
  entry(head, e.period, items: e.bullets)
}

// ---- more ----
#if cv.rows.len() > 0 {
  section(titles.more)
  v(0.1cm * sp)
  for r in cv.rows {
    let body = if r.items == "" [*#r.label:*] else if r.indent [#r.label: #r.items] else [*#r.label:* #r.items]
    block(above: 0.12cm * sp, below: 0pt,
          inset: (left: if r.indent { 0.4cm } else { 0pt }), body)
  }
}
