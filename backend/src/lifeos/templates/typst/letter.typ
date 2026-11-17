// Cover letter: sender left, recipient right, date, subject, salutation, paragraphs.
// All content comes from sys.inputs.data (JSON); nothing personal lives in this file.
#let d = json(bytes(sys.inputs.at("data")))
#let p = d.personal
#let l = d.letter

#set document(title: p.name + " – " + l.subject, author: p.name)
#set page(paper: "a4", margin: 2cm)
#set text(font: "XCharter", size: 10pt, lang: "de")
#set par(justify: true, leading: 0.78em, spacing: 1.1em)

#grid(
  columns: (1fr, 1fr),
  {
    set par(justify: false, spacing: 0.78em)
    let lines = (p.name, p.street, p.city)
    if p.phone != "" { lines.push(p.phone) }
    if p.email != "" { lines.push(link("mailto:" + p.email, p.email)) }
    if p.linkedin != "" { lines.push(link("https://" + p.linkedin, p.linkedin)) }
    lines.join(linebreak())
  },
  align(right, {
    set par(justify: false)
    l.recipient.join(linebreak())
  }),
)

#v(1.2cm)
#align(right, d.date)
#v(1.5cm)
#text(weight: "bold", l.subject)
#v(2.5cm)
#l.salutation
#v(0.4cm)

#for paragraph in l.paragraphs [
  #paragraph

]

#v(0.8cm)
#set par(justify: false, spacing: 0.78em)
#l.closing \
#p.name
