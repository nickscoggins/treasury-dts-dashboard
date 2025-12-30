import streamlit as st

st.set_page_config(page_title='US Treasury Dashboard', layout='wide')

st.title('US Treasury Daily Treasury Statement (DTS) Dashboard')
st.caption('MVP: Deposits and Withdrawals of Operating Cash -> Sankey Flows Through the Treasury General Account (TGA)')

st.info('Next: connect your Deposits/Withdrawals CSV + mapping file and render the first Sankey.')